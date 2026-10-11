"""The mark and the comment a user puts on a result, which is a version of a page.

A result carries one mark, good or bad or none, and one comment. Both are notes of the user on the result, no input of
the step, so changing them leaves the identifier of the version, its files and the stage records alone. Both can be
changed at any time, and every change is written to the log of the version with the mark and the comment before and
after, which is never rewritten. A change that leaves both as they are writes nothing. A preview is not a result the
user keeps, since a collection deletes it, so it takes no mark.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import ResultMarkChange
from bookreviver.domain.enums import VersionScale
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import ResultMarkChangeId
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, PageVersion
    from bookreviver.domain.ids import PageId, PageVersionId, ProjectId
    from bookreviver.domain.values import ResultNote
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock

PREVIEW_TAKES_NO_MARK: str = 'The version {version_id} is a preview, which takes no mark and no comment.'


class ResultMarksService:
    """Sets and lists the marks and comments of the results of the acting account's projects."""

    def __init__(self, *, uow: UnitOfWork, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose blocks end every changing use case.
        :type uow: UnitOfWork
        :param clock: Clock stamping the changes.
        :type clock: Clock
        """
        self._uow = uow
        self._clock = clock

    async def set(
        self,
        actor: Actor,
        project_id: ProjectId,
        page_id: PageId,
        version_id: PageVersionId,
        note: ResultNote,
    ) -> PageVersion:
        """Replace the mark and the comment of a result, and write the change to its log.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :param note: The mark and the comment from now on, which replace both.
        :type note: ResultNote
        :returns: The version with its new mark and comment, which is the stored one when nothing changed.
        :rtype: PageVersion
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        :raises ConflictError: If the version is a preview.
        """
        async with self._uow.change_book(project_id):
            version = await self._version(actor, project_id, page_id, version_id)
            if version.scale is not VersionScale.FULL:
                raise ConflictError(PREVIEW_TAKES_NO_MARK.format(version_id=version_id))
            if (note.mark, note.comment) == (version.mark, version.comment):
                return version
            changed = evolve(version, mark=note.mark, comment=note.comment)
            await self._uow.page_versions.update(changed)
            await self._uow.result_mark_changes.add(
                ResultMarkChange(
                    id=ResultMarkChangeId(uuid4()),
                    version_id=version.id,
                    mark_before=version.mark,
                    mark_after=note.mark,
                    comment_before=version.comment,
                    comment_after=note.comment,
                    created_at=self._clock.now(),
                )
            )
        return changed

    async def changes(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, version_id: PageVersionId
    ) -> Sequence[ResultMarkChange]:
        """List the changes of the mark and the comment of a result, the earliest first.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The log of the version.
        :rtype: Sequence[ResultMarkChange]
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        """
        version = await self._version(actor, project_id, page_id, version_id)
        return await self._uow.result_mark_changes.list_for_version(version.id)

    async def _version(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, version_id: PageVersionId
    ) -> PageVersion:
        """Return a version of a page of the actor's project.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        """
        await owned_project(self._uow.projects, actor, project_id)
        if (await self._uow.pages.get(page_id)).project_id != project_id:
            raise NotFoundError(page_id)
        version = await self._uow.page_versions.get(version_id)
        if version.page_id != page_id:
            raise NotFoundError(version_id)
        return version
