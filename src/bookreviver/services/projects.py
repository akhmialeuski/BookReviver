"""Use cases of projects: listing, creating, describing and deleting books.

Every lookup of a project goes through ``owned_project``, which reports a project of another account exactly like a
missing one. An answer of "forbidden" would confirm that a project with that identifier exists, so the identifiers
of other accounts' projects could be probed. The lookups ``owned_project``, ``owned_page`` and ``book_pages`` are
synchronous functions over repositories: the work of ``UnitOfWork.read``, ``change`` or ``change_book`` calls them
with the repositories it receives, and a caller outside a transaction wraps the call in ``UnitOfWork.read``.

A project is deleted files first and row last. Every lookup, a repeated deletion included, needs the row, so a row
deleted first would leave the files of a failed deletion out of every request's reach for good. Both stores treat a
project without files as deleted, so a deletion that fails part-way keeps the project, and repeating it removes the
files that are left and then the row. Until then the project may lack some of its files, which only a deletion the
owner asked for can cause. The files go outside any transaction, and the row goes in a ``change_book`` of its own.
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
    from bookreviver.domain.entities import Actor, Page
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, StepId
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import PageRepository, ProjectRepository, Repositories, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, SourceStore
    from bookreviver.services.stage_summaries import StageSummaries

# How many pages are read from the book at a time, when a use case needs all of them
PAGE_WINDOW: int = 1_000


def require_owner(project: Project, actor: Actor) -> Project:
    """Return a project that is read already, which the work of ``change_book`` receives, when the actor owns it.

    :param project: Project as read.
    :type project: Project
    :param actor: Account acting in the current request.
    :type actor: Actor
    :returns: The same project, owned by the actor.
    :rtype: Project
    :raises NotFoundError: If the project belongs to another account.
    """
    if not project.is_owned_by(actor):
        raise NotFoundError(project.id)
    return project


def owned_project(projects: ProjectRepository, actor: Actor, project_id: ProjectId) -> Project:
    """Return the actor's project, which is called from the work of a transaction.

    :param projects: Repository of the transaction to read the project from.
    :type projects: ProjectRepository
    :param actor: Account acting in the current request.
    :type actor: Actor
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :returns: The project, owned by the actor.
    :rtype: Project
    :raises NotFoundError: If the project does not exist or belongs to another account.
    """
    return require_owner(projects.get(project_id), actor)


def owned_page(repos: Repositories, actor: Actor, project_id: ProjectId, page_id: PageId) -> Page:
    """Return a page of the actor's project, which is called from the work of a transaction.

    :param repos: Repositories of the transaction to read the project and the page from.
    :type repos: Repositories
    :param actor: Account acting in the current request.
    :type actor: Actor
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param page_id: Identifier of the page.
    :type page_id: PageId
    :returns: The page, which is one of the book of the project.
    :rtype: Page
    :raises NotFoundError: If the actor has no such project, or the project has no such page.
    """
    owned_project(repos.projects, actor, project_id)
    page = repos.pages.get(page_id)
    if page.project_id != project_id:
        raise NotFoundError(page_id)
    return page


def book_pages(pages: PageRepository, project_id: ProjectId) -> list[Page]:
    """Read every page of a project in book order, window by window, which is called from the work of a transaction.

    :param pages: Repository of the transaction to read the pages from.
    :type pages: PageRepository
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :returns: The pages of the book, the placeholders and the pages kept out of the book included.
    :rtype: list[Page]
    """
    book: list[Page] = []
    while True:
        window = pages.list_for_project(project_id, SliceRequest(offset=len(book), limit=PAGE_WINDOW))
        book.extend(window.items)
        if len(book) >= window.total or not window.items:
            return book


class ProjectService:
    """Projects of the acting account, with their description and their files."""

    def __init__(
        self, *, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore, stages: StageSummaries
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, which runs the work of every use case in one transaction.
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

        def work(repos: Repositories) -> Slice[ProjectOverview]:
            """Read the window of projects and add the progress of each book.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The projects of the window with their progress, and the number of all the actor's projects.
            :rtype: Slice[ProjectOverview]
            """
            window = repos.projects.list_for_owner(actor.account_id, request)
            return Slice(items=self._stages.with_progress(repos, window.items), total=window.total)

        return await self._uow.read(work)

    async def create(self, actor: Actor, details: BookDetails) -> ProjectOverview:
        """Create an empty project owned by the actor, in a ``change``, since no book exists to lock yet.

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
        stored = await self._uow.change(lambda repos: repos.projects.add(project))
        return await self._uow.read(lambda repos: self._with_progress(repos, ProjectOverview(project=stored)))

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

        def work(repos: Repositories) -> ProjectOverview:
            """Read the project of the actor with the counts and the progress of its book.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The project with the counts and the progress of its book.
            :rtype: ProjectOverview
            :raises NotFoundError: If the actor has no such project.
            """
            project = owned_project(repos.projects, actor, project_id)
            return self._with_progress(repos, repos.projects.overview(project))

        return await self._uow.read(work)

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

        def work(repos: Repositories) -> Slice[StageSummary]:
            """Sum every stage of the book and cut the window the request asks for.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The window of the stages in the order of the pipeline, and the number of all the stages.
            :rtype: Slice[StageSummary]
            :raises NotFoundError: If the actor has no such project.
            """
            project = owned_project(repos.projects, actor, project_id)
            summaries = self._stages.of_book(repos, repos.projects.overview(project))
            return Slice(items=summaries[request.offset : request.offset + request.limit], total=len(summaries))

        return await self._uow.read(work)

    async def stage_pages(
        self, actor: Actor, project_id: ProjectId, stage: Stage, request: SliceRequest, step_id: StepId | None = None
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
        :param step_id: A step of a recipe of the stage to place each page at, or None for the rows of the stage alone.
        :type step_id: StepId | None
        :returns: The rows of the window in book order, and the number of pages of the book.
        :rtype: Slice[StageRow]
        :raises NotFoundError: If the actor has no such project, or no recipe of the stage has the step.
        """

        def work(repos: Repositories) -> Slice[StageRow]:
            """Read the window of rows of the stage over the pages of the actor's book.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The rows of the window in book order, and the number of pages of the book.
            :rtype: Slice[StageRow]
            :raises NotFoundError: If the actor has no such project, or no recipe of the stage has the step.
            """
            project = owned_project(repos.projects, actor, project_id)
            return self._stages.rows(repos, project, stage, request, step_id)

        return await self._uow.read(work)

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

        def change(repos: Repositories, project: Project) -> Project:
            """Store the project with the changed fields.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The project as stored.
            :rtype: Project
            :raises NotFoundError: If the actor has no such project, or the new cover is not a page of the project.
            :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
            """
            changed = evolve(changes.apply_to(require_owner(project, actor)), updated_at=self._clock.now())
            return repos.projects.update(changed)

        stored = await self._uow.change_book(project_id, change)
        return await self._uow.read(lambda repos: self._with_progress(repos, repos.projects.overview(stored)))

    def _with_progress(self, repos: Repositories, overview: ProjectOverview) -> ProjectOverview:
        """Add the progress of the book to the overview of one project.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param overview: The project with the counts of its book.
        :type overview: ProjectOverview
        :returns: The same project with its progress.
        :rtype: ProjectOverview
        """
        [with_progress] = self._stages.with_progress(repos, [overview])
        return with_progress

    async def delete(self, actor: Actor, project_id: ProjectId) -> None:
        """Delete the project's source files and derived files, then the project with every row of its book.

        The owner is checked before any file goes, and the files go outside any transaction, so a slow store holds no
        lock of the book. Only the row goes in a ``change_book``, which this use case opens, so it is called outside a
        transaction. A failure at any step leaves the project in place, and calling this again finishes the deletion.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :raises NotFoundError: If the actor has no such project.
        """
        await self._uow.read(lambda repos: owned_project(repos.projects, actor, project_id))
        # The row goes last: only an existing row lets a repeated call reach files a failed call left behind
        await self._sources.delete_project(project_id)
        await self._assets.delete_project(project_id)
        await self._uow.change_book(project_id, lambda repos, _project: repos.projects.delete(project_id))

    async def delete_all(self, actor: Actor) -> None:
        """Delete every project of the actor with all its files, which must happen before its account is deleted.

        Each project is deleted in a transaction of its own, files first, so a failure keeps the projects not yet
        deleted, and calling this again deletes them. The first window of the actor's projects is read again after
        every window, since the deleted projects leave it.

        :param actor: Account whose projects are deleted.
        :type actor: Actor
        """
        while True:
            window = await self._uow.read(lambda repos: repos.projects.list_for_owner(actor.account_id, SliceRequest()))
            if not window.items:
                return
            for overview in window.items:
                await self.delete(actor, overview.project.id)
