"""The deletion of a batch of versions a collection chose, which keeps every chain of inputs whole.

A version is deleted with its files, its row and the log of its marks, in this order: the mark first, so a version whose
files are half removed is never taken for a result, then the directory, and the row only when the directory is gone. A
batch is written by two blocks, one for the marks and one for the rows, so a version whose directory cannot be removed
keeps its mark and its row and takes back nothing of the others.

The versions of a collection are given to ``VersionClearing`` readers first, as ``readers_first`` groups them. A version
that stays, because its files could not be removed, holds the version it reads, and so does every version that is held,
since deleting an input would leave the reader with none, which would make it look like a base version for ever. A
version held is left alone, and the next collection chooses it again once its reader is gone.
"""

import logging
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import VersionData, VersionState
from bookreviver.domain.keys import ProjectKeys

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from bookreviver.domain.entities import PageVersion
    from bookreviver.domain.ids import PageVersionId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore

BEING_COLLECTED: str = 'The version is being deleted.'

logger = logging.getLogger(__name__)


class VersionClearing:
    """Deletes batches of the versions of one project, and remembers the versions that must stay."""

    def __init__(self, *, uow: UnitOfWork, assets: AssetStore, project_id: ProjectId) -> None:
        """Work over the ports of one collection of one project.

        :param uow: Unit of work of the job, whose blocks write each batch.
        :type uow: UnitOfWork
        :param assets: Store of the derived files, from which the directories of the versions are removed.
        :type assets: AssetStore
        :param project_id: Project owning the versions.
        :type project_id: ProjectId
        """
        self._uow = uow
        self._assets = assets
        self._project_id = project_id
        self._keys = ProjectKeys(project_id)
        self._held: set[PageVersionId] = set()

    async def clear(self, batch: Sequence[PageVersion]) -> int:
        """Delete the versions of a batch that no version which stays reads.

        It opens two ``change_book`` blocks, the first marking the versions and the second deleting the rows of those
        whose files are gone, with the removal of the files between them, outside any block. So it is called only
        outside a block.

        :param batch: Versions that read none of each other and are read by none of the versions cleared later, taken
                      from one group of ``readers_first``.
        :type batch: Sequence[PageVersion]
        :returns: The number of versions deleted.
        :rtype: int
        """
        going = [version for version in batch if version.id not in self._held]
        self._hold(version for version in batch if version.id in self._held)
        async with self._uow.change_book(self._project_id):
            # The versions are read again, so a version deleted meanwhile is left out and a mark or a comment a user
            # put on one since the collection chose it is kept
            for version in await self._uow.page_versions.list_by_ids([version.id for version in going]):
                if version.state is VersionState.READY:
                    marked = evolve(
                        version, state=VersionState.FAILED, data={**version.data, VersionData.ERROR: BEING_COLLECTED}
                    )
                    await self._uow.page_versions.update(marked)
        gone: list[PageVersionId] = []
        for version in going:
            try:
                await self._assets.delete_prefix(self._keys.version_directory(version))
            except OSError:
                logger.exception('The files of the version %s were not removed, so its row stays', version.id)
                self._hold([version])
            else:
                gone.append(version.id)
        async with self._uow.change_book(self._project_id):
            await self._uow.page_versions.delete_many(gone)
        return len(gone)

    def _hold(self, staying: Iterable[PageVersion]) -> None:
        """Keep the inputs of the versions that stay out of the deletions that follow.

        :param staying: Versions that are not deleted.
        :type staying: Iterable[PageVersion]
        """
        self._held.update(version.input_id for version in staying if version.input_id is not None)
