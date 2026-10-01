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
from bookreviver.domain.values import Slice, SliceRequest

if TYPE_CHECKING:
    from bookreviver.domain.changes import ProjectChanges
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import ProjectRepository, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, SourceStore
    from bookreviver.services.stage_summaries import StageSummaries


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

    def __init__(
        self, *, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore, stages: StageSummaries
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param clock: Clock setting the creation and update times.
        :type clock: Clock
        :param sources: Store of the uploaded sources, emptied when a project is deleted.
        :type sources: SourceStore
        :param assets: Store of the derived files, emptied when a project is deleted.
        :type assets: AssetStore
        :param stages: Sums of the stages of books, which give each project its progress.
        :type stages: StageSummaries
        """
        self._uow = uow
        self._clock = clock
        self._sources = sources
        self._assets = assets
        self._stages = stages

    async def list(self, actor: Actor, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return a window of the actor's projects, most recently updated first.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The projects of the window with the counts and the progress of their books, and the number of all the
                  actor's projects.
        :rtype: Slice[ProjectOverview]
        """
        window = await self._uow.projects.list_for_owner(actor.account_id, request)
        return Slice(items=await self._stages.with_progress(window.items), total=window.total)

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
        return await self._with_progress(ProjectOverview(project=stored))

    async def get(self, actor: Actor, project_id: ProjectId) -> ProjectOverview:
        """Return one of the actor's projects.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The project with the counts and the progress of its book.
        :rtype: ProjectOverview
        :raises NotFoundError: If the actor has no such project.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        return await self._with_progress(await self._uow.projects.overview(project))

    async def stages(self, actor: Actor, project_id: ProjectId, request: SliceRequest) -> Slice[StageSummary]:
        """Return the stages of one of the actor's projects, each summed over the pages of the book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: Offset and limit of the window of stages.
        :type request: SliceRequest
        :returns: The window of the stages in the order of the pipeline, and the number of all the stages.
        :rtype: Slice[StageSummary]
        :raises NotFoundError: If the actor has no such project.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        summaries = await self._stages.of_book(await self._uow.projects.overview(project))
        return Slice(items=summaries[request.offset : request.offset + request.limit], total=len(summaries))

    async def stage_pages(
        self, actor: Actor, project_id: ProjectId, stage: Stage, request: SliceRequest
    ) -> Slice[StageRow]:
        """Return a window of the pages of one of the actor's projects, each with where it stands in a stage.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window of pages.
        :type request: SliceRequest
        :returns: The rows of the window in book order, and the number of pages of the book.
        :rtype: Slice[StageRow]
        :raises NotFoundError: If the actor has no such project.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        return await self._stages.rows(project, stage, request)

    async def update(self, actor: Actor, project_id: ProjectId, changes: ProjectChanges) -> ProjectOverview:
        """Change some fields of the project, such as its description or its cover, and mark it as updated.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param changes: New values of the fields to change.
        :type changes: ProjectChanges
        :returns: The changed project with the counts and the progress of its book.
        :rtype: ProjectOverview
        :raises NotFoundError: If the actor has no such project, or the new cover is not a page of the project.
        :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        changed = evolve(changes.apply_to(project), updated_at=self._clock.now())
        stored = await self._uow.projects.update(changed)
        await self._uow.commit()
        return await self._with_progress(await self._uow.projects.overview(stored))

    async def _with_progress(self, overview: ProjectOverview) -> ProjectOverview:
        """Add the progress of the book to the overview of one project.

        :param overview: The project with the counts of its book.
        :type overview: ProjectOverview
        :returns: The same project with its progress.
        :rtype: ProjectOverview
        """
        [with_progress] = await self._stages.with_progress([overview])
        return with_progress

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
