"""Use cases of the sources of a book and their scans: listing, reading and deleting.

A source is an uploaded file kept as it was uploaded, and its scans are the images inside it. Both are read-only
records of where the book came from, and the book's pages hold copies of their own images, so a source can be deleted
without touching a page. Deleting one removes its files, its scans and the derived files of its scans, and the pages
made from those scans only lose the reference to them.

A source is deleted files first and row last, like a project. Every lookup, a repeated deletion included, needs the
row, so a row deleted first would leave the files of a failed deletion out of every request's reach. Both stores treat
a missing source as deleted, so a deletion that fails part-way keeps the source, and repeating it finishes the job. A
source whose import keeps failing on one of its scans is left by this deletion too: the failed scan is named in the
result of the import job, and the user deletes the source by hand. Nothing skips a scan automatically.

A source is not deleted while the project imports, because the import may be writing the renditions of its scans.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Slice
from bookreviver.services.imports import IMPORT_JOBS
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Scan, Source
    from bookreviver.domain.ids import ProjectId, SourceId
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore, SourceStore

SOURCE_IMPORTING: str = 'This project is importing files. Delete the source when the import has finished.'


def _window[ItemT](items: Sequence[ItemT], request: SliceRequest) -> Slice[ItemT]:
    """Cut a window out of a whole collection that is small enough to be read at once.

    :param items: Every item of the collection, in collection order.
    :type items: Sequence[ItemT]
    :param request: Offset and limit of the window.
    :type request: SliceRequest
    :returns: The items of the window and the number of all the items.
    :rtype: Slice[ItemT]
    """
    return Slice(items=items[request.offset : request.offset + request.limit], total=len(items))


class SourceService:
    """Sources and scans of the acting account's projects, and the deletion of a source with its files."""

    def __init__(self, *, uow: UnitOfWork, sources: SourceStore, assets: AssetStore) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends the deletion of a source.
        :type uow: UnitOfWork
        :param sources: Store of the uploaded sources, emptied of a source when it is deleted.
        :type sources: SourceStore
        :param assets: Store of the derived files, emptied of the renditions of a source's scans when it is deleted.
        :type assets: AssetStore
        """
        self._uow = uow
        self._sources = sources
        self._assets = assets

    async def list(self, actor: Actor, project_id: ProjectId, request: SliceRequest) -> Slice[Source]:
        """Return a window of the project's sources in the order they were imported.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The sources of the window and the number of all the project's sources.
        :rtype: Slice[Source]
        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return _window(await self._uow.sources.list_for_project(project_id), request)

    async def get(self, actor: Actor, project_id: ProjectId, source_id: SourceId) -> Source:
        """Return one source of the project.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param source_id: Identifier of the source.
        :type source_id: SourceId
        :returns: The source.
        :rtype: Source
        :raises NotFoundError: If the actor has no such project, or the project has no such source.
        """
        return await self._owned_source(actor, project_id, source_id)

    async def scans(
        self, actor: Actor, project_id: ProjectId, request: SliceRequest, *, source_id: SourceId | None = None
    ) -> Slice[Scan]:
        """Return a window of the scans of the project, or of one of its sources, in the order of the book.

        The scans of a project come source by source in import order, and the scans of a source by their numbers.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param source_id: Source whose scans are listed, or None for the scans of every source of the project.
        :type source_id: SourceId | None
        :returns: The scans of the window and the number of all the scans listed.
        :rtype: Slice[Scan]
        :raises NotFoundError: If the actor has no such project, or the project has no such source.
        """
        if source_id is None:
            await owned_project(self._uow.projects, actor, project_id)
            return await self._uow.scans.list_for_project(project_id, request)
        await self._owned_source(actor, project_id, source_id)
        return _window(await self._uow.scans.list_for_source(source_id), request)

    async def delete(self, actor: Actor, project_id: ProjectId, source_id: SourceId) -> None:
        """Delete a source's files and the renditions of its scans, then the source with its scans.

        The pages of the book made from the scans keep their images, versions, numbers and order, and lose only the
        reference to their scan. A failure at any step leaves the source in place, and calling this again finishes the
        deletion.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param source_id: Identifier of the source.
        :type source_id: SourceId
        :raises NotFoundError: If the actor has no such project, or the project has no such source.
        :raises ConflictError: If the project has an import queued or running.
        """
        await self._owned_source(actor, project_id, source_id)
        active = await self._uow.jobs.list_for_project(project_id, JobState.active())
        if any(job.kind in IMPORT_JOBS for job in active):
            raise ConflictError(SOURCE_IMPORTING)
        # The row goes last: only an existing row lets a repeated call reach files a failed call left behind
        await self._sources.delete_source(project_id, source_id)
        await self._assets.delete_prefix(ProjectKeys(project_id).source_scans(source_id))
        await self._uow.sources.delete(source_id)
        await self._uow.commit()

    async def _owned_source(self, actor: Actor, project_id: ProjectId, source_id: SourceId) -> Source:
        """Return a source of the actor's project.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param source_id: Identifier of the source.
        :type source_id: SourceId
        :returns: The source, which belongs to the project.
        :rtype: Source
        :raises NotFoundError: If the actor has no such project, or the project has no such source; a source of
                               another project is reported like a missing one.
        """
        await owned_project(self._uow.projects, actor, project_id)
        source = await self._uow.sources.get(source_id)
        if source.project_id != project_id:
            raise NotFoundError(source_id)
        return source
