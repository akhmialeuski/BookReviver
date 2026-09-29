"""Use cases of projects: listing, creating, describing and deleting books.

Every lookup of a project goes through ``owned_project``, which reports a project of another account exactly like a
missing one. An answer of "forbidden" would confirm that a project with that identifier exists, so the identifiers
of other accounts' projects could be probed.

A project is deleted row first and files second. The files go only once the deletion of the row is committed, so a
failing store leaves orphan files, which a repeated deletion removes, and never a project whose files are gone.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import Project, ProjectOverview
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.values import SliceRequest

if TYPE_CHECKING:
    from bookreviver.domain.changes import BookDetailsChanges
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.values import BookDetails, Slice
    from bookreviver.ports.persistence import ProjectRepository, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, SourceStore

# The smallest window of pages; only the total of the result is read
PAGE_COUNT_REQUEST: SliceRequest = SliceRequest(limit=1)


async def owned_project(projects: ProjectRepository, actor: Actor, project_id: ProjectId) -> Project:
    """Return the actor's project.

    :param projects: Repository to read the project from.
    :type projects: ProjectRepository
    :param actor: Account acting in the current request.
    :type actor: Actor
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :returns: The project, owned by the actor.
    :rtype: Project
    :raises NotFoundError: If the project does not exist or belongs to another account.
    """
    project = await projects.get(project_id)
    if not project.is_owned_by(actor):
        raise NotFoundError(project_id)
    return project


class ProjectService:
    """Projects of the acting account, with their description and their files."""

    def __init__(self, *, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param clock: Clock setting the creation and update times.
        :type clock: Clock
        :param sources: Store of the uploaded sources, emptied when a project is deleted.
        :type sources: SourceStore
        :param assets: Store of the derived files, emptied when a project is deleted.
        :type assets: AssetStore
        """
        self._uow = uow
        self._clock = clock
        self._sources = sources
        self._assets = assets

    async def list(self, actor: Actor, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return a window of the actor's projects, most recently updated first.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The projects of the window with their page counts, and the number of all the actor's projects.
        :rtype: Slice[ProjectOverview]
        """
        return await self._uow.projects.list_for_owner(actor.account_id, request)

    async def create(self, actor: Actor, details: BookDetails) -> ProjectOverview:
        """Create an empty project owned by the actor.

        :param actor: Account acting in the current request, which becomes the owner.
        :type actor: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The stored project, which has no pages yet.
        :rtype: ProjectOverview
        """
        moment = self._clock.now()
        project = Project(
            id=ProjectId(uuid4()), owner_id=actor.account_id, details=details, created_at=moment, updated_at=moment
        )
        stored = await self._uow.projects.add(project)
        await self._uow.commit()
        return ProjectOverview(project=stored, page_count=0)

    async def get(self, actor: Actor, project_id: ProjectId) -> ProjectOverview:
        """Return one of the actor's projects.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The project with the number of its pages.
        :rtype: ProjectOverview
        :raises NotFoundError: If the actor has no such project.
        """
        return await self._overview(await owned_project(self._uow.projects, actor, project_id))

    async def update_details(self, actor: Actor, project_id: ProjectId, changes: BookDetailsChanges) -> ProjectOverview:
        """Change some fields of the project's description and mark the project as updated.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param changes: New values of the fields to change.
        :type changes: BookDetailsChanges
        :returns: The changed project with the number of its pages.
        :rtype: ProjectOverview
        :raises NotFoundError: If the actor has no such project.
        :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        changed = evolve(project, details=changes.apply_to(project.details), updated_at=self._clock.now())
        stored = await self._uow.projects.update(changed)
        await self._uow.commit()
        return await self._overview(stored)

    async def delete(self, actor: Actor, project_id: ProjectId) -> None:
        """Delete the project with its pages and jobs, then its source and its derived files.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._uow.projects.delete(project_id)
        await self._uow.commit()
        # Files go only once the row is gone, so a failure leaves orphan files rather than a project without files
        await self._sources.delete_project(project_id)
        await self._assets.delete_project(project_id)

    async def _overview(self, project: Project) -> ProjectOverview:
        """Pair a project with the number of its pages.

        :param project: Project to describe.
        :type project: Project
        :returns: The project with the number of its pages.
        :rtype: ProjectOverview
        """
        pages = await self._uow.pages.list_for_project(project.id, PAGE_COUNT_REQUEST)
        return ProjectOverview(project=project, page_count=pages.total)
