"""Use cases of projects: listing, creating, describing and deleting books.

Every lookup of a project goes through ``owned_project``, which reports a project of another account exactly like a
missing one. An answer of "forbidden" would confirm that a project with that identifier exists, so the identifiers
of other accounts' projects could be probed.

A project is deleted files first and row last. Every lookup, a repeated deletion included, needs the row, so a row
deleted first would leave the files of a failed deletion out of every request's reach for good. Both stores treat a
project without files as deleted, so a deletion that fails part-way keeps the project, and repeating it removes the
files that are left and then the row. Until then the project may lack some of its files, which only a deletion the
owner asked for can cause.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import Project, ProjectOverview
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.values import SliceRequest

if TYPE_CHECKING:
    from bookreviver.domain.changes import ProjectChanges
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.values import BookDetails, Slice
    from bookreviver.ports.persistence import ProjectRepository, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, SourceStore


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
        :returns: The projects of the window with the counts of their books, and the number of all the actor's
                  projects.
        :rtype: Slice[ProjectOverview]
        """
        return await self._uow.projects.list_for_owner(actor.account_id, request)

    async def create(self, actor: Actor, details: BookDetails) -> ProjectOverview:
        """Create an empty project owned by the actor.

        :param actor: Account acting in the current request, which becomes the owner.
        :type actor: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The stored project, which has no pages, sources or scans yet.
        :rtype: ProjectOverview
        """
        moment = self._clock.now()
        project = Project(
            id=ProjectId(uuid4()), owner_id=actor.account_id, details=details, created_at=moment, updated_at=moment
        )
        stored = await self._uow.projects.add(project)
        await self._uow.commit()
        return ProjectOverview(project=stored)

    async def get(self, actor: Actor, project_id: ProjectId) -> ProjectOverview:
        """Return one of the actor's projects.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The project with the counts of its book.
        :rtype: ProjectOverview
        :raises NotFoundError: If the actor has no such project.
        """
        return await self._uow.projects.overview(await owned_project(self._uow.projects, actor, project_id))

    async def update(self, actor: Actor, project_id: ProjectId, changes: ProjectChanges) -> ProjectOverview:
        """Change some fields of the project, such as its description or its cover, and mark it as updated.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param changes: New values of the fields to change.
        :type changes: ProjectChanges
        :returns: The changed project with the counts of its book.
        :rtype: ProjectOverview
        :raises NotFoundError: If the actor has no such project, or the new cover is not a page of the project.
        :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        changed = evolve(changes.apply_to(project), updated_at=self._clock.now())
        stored = await self._uow.projects.update(changed)
        await self._uow.commit()
        return await self._uow.projects.overview(stored)

    async def delete(self, actor: Actor, project_id: ProjectId) -> None:
        """Delete the project's source files and derived files, then the project with every row of its book.

        A failure at any step leaves the project in place, and calling this again finishes the deletion.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        # The row goes last: only an existing row lets a repeated call reach files a failed call left behind
        await self._sources.delete_project(project_id)
        await self._assets.delete_project(project_id)
        await self._uow.projects.delete(project_id)
        await self._uow.commit()

    async def delete_all(self, actor: Actor) -> None:
        """Delete every project of the actor with all its files, which must happen before its account is deleted.

        Each project is deleted and committed on its own, files first, so a failure keeps the projects not yet
        deleted, and calling this again deletes them. The first window of the actor's projects is read again after
        every window, since the deleted projects leave it.

        :param actor: Account whose projects are deleted.
        :type actor: Actor
        """
        while projects := (await self._uow.projects.list_for_owner(actor.account_id, SliceRequest())).items:
            for overview in projects:
                await self.delete(actor, overview.project.id)
