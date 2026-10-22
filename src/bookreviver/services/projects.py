"""Use cases of projects: listing, creating, describing, exporting, importing and deleting books.

Every use case of a book with an actor first asks the ``Library`` to open the book, which reports a book of another
account exactly like a missing one. An answer of "forbidden" would confirm that a book with that identifier exists, so
the identifiers of other accounts' books could be probed. The lookups ``book_page`` and ``book_pages`` are synchronous
functions over repositories: the work of ``UnitOfWork.read_book`` or ``change_book`` calls them with the repositories
it receives, and a caller outside a transaction wraps the call in ``UnitOfWork.read_book``.

A book is deleted files first, then its store, and the library entry last. Every use case of the book needs the entry,
a repeated deletion included, so an entry deleted first would leave the files of a failed deletion out of every
request's reach for good. A deletion that fails part-way keeps the entry, and repeating it removes what is left. Until
then the book may lack some of its files, which only a deletion the owner asked for can cause. The files go outside any
transaction. A start pass, ``sweep``, finishes a deletion or a creation that was broken off half way and is not
repeated by anyone: a store without an entry, or an entry without a store.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import LibraryEntry, Project
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.values import ArchivedBook, Slice, SliceRequest

if TYPE_CHECKING:
    from typing import BinaryIO

    from bookreviver.domain.changes import ProjectChanges
    from bookreviver.domain.entities import Actor, Page, ProjectOverview
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import StepId
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import BookRepositories, PageRepository, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, BookArchive, SourceStore
    from bookreviver.services.library import Library
    from bookreviver.services.stage_summaries import StageSummaries

# How many pages are read from the book at a time, when a use case needs all of them
PAGE_WINDOW: int = 1_000


def book_pages(pages: PageRepository) -> list[Page]:
    """Read every page of the book in book order, window by window, which a transaction of the book calls.

    :param pages: Repository of the transaction to read the pages from.
    :type pages: PageRepository
    :returns: The pages of the book, the placeholders and the pages kept out of the book included.
    :rtype: list[Page]
    """
    book: list[Page] = []
    while True:
        window = pages.list_window(SliceRequest(offset=len(book), limit=PAGE_WINDOW))
        book.extend(window.items)
        if len(book) >= window.total or not window.items:
            return book


@frozen(kw_only=True)
class ProjectFiles:
    """The stores of the files of a book, emptied when the book is deleted, and the archive that carries them all.

    :ivar sources: Store of the uploaded sources.
    :ivar assets: Store of the derived files.
    :ivar archive: Archive of a whole book, which packs a book and unpacks one as a new book.
    """

    sources: SourceStore
    assets: AssetStore
    archive: BookArchive


class ProjectService:
    """Projects of the acting account, with their description and their files."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        library: Library,
        clock: Clock,
        files: ProjectFiles,
        stages: StageSummaries,
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, which runs the work of every use case in one transaction.
        :type uow: UnitOfWork
        :param library: Library that opens the books of the acting account.
        :type library: Library
        :param clock: Clock setting the creation and update times.
        :type clock: Clock
        :param files: Stores of the source files and the derived files, emptied when a project is deleted.
        :type files: ProjectFiles
        :param stages: Sums of the stages of books, which give each project its progress.
        :type stages: StageSummaries
        """
        self._uow = uow
        self._library = library
        self._clock = clock
        self._files = files
        self._stages = stages

    async def list(self, actor: Actor, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return a window of the actor's projects, the latest added first.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The projects of the window with the counts and the progress of their books, and the number of all the
                  actor's projects.
        :rtype: Slice[ProjectOverview]
        """
        window = await self._uow.read(lambda repos: repos.library.list_for_owner(actor.account_id, request))
        return Slice(items=[await self._overview(entry.project_id) for entry in window.items], total=window.total)

    async def create(self, actor: Actor, details: BookDetails) -> ProjectOverview:
        """Create an empty project owned by the actor: the store of its book first, then its entry in the library.

        :param actor: Account acting in the current request, which becomes the owner.
        :type actor: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The stored project, which has no pages, sources or scans yet.
        :rtype: ProjectOverview
        """
        moment = self._clock.now()
        project = await self._uow.create_book(
            Project(id=ProjectId(uuid4()), details=details, created_at=moment, updated_at=moment)
        )
        entry = LibraryEntry(project_id=project.id, owner_id=actor.account_id, added_at=moment)
        await self._uow.change(lambda repos: repos.library.add(entry))
        return await self._overview(project.id)

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
        await self._library.open(actor, project_id)
        return await self._overview(project_id)

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
        await self._library.open(actor, project_id)

        def work(repos: BookRepositories) -> Slice[StageSummary]:
            """Sum every stage of the book and cut the window the request asks for.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The window of the stages in the order of the pipeline, and the number of all the stages.
            :rtype: Slice[StageSummary]
            """
            summaries = self._stages.of_book(repos, repos.projects.overview(repos.projects.get(project_id)))
            return Slice(items=summaries[request.offset : request.offset + request.limit], total=len(summaries))

        return await self._uow.read_book(project_id, work)

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
        await self._library.open(actor, project_id)
        return await self._uow.read_book(
            project_id,
            lambda repos: self._stages.rows(repos, stage, request, step_id),
        )

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
        await self._library.open(actor, project_id)

        def change(repos: BookRepositories, project: Project) -> Project:
            """Store the project with the changed fields.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The project as stored.
            :rtype: Project
            :raises NotFoundError: If the new cover is not a page of the project.
            :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
            """
            return repos.projects.update(evolve(changes.apply_to(project), updated_at=self._clock.now()))

        await self._uow.change_book(project_id, change)
        return await self._overview(project_id)

    async def export(self, actor: Actor, project_id: ProjectId) -> ArchivedBook:
        """Return the archive of one of the actor's books, which another instance imports as a book of its own.

        The database is copied first, as one committed state, and the files after it. A file is written once and never
        replaced, so every file the copy names is on the disk when the archive reads it, unless the book is deleted or
        a source of it removed while the archive is read.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The title of the book and the stream of its archive.
        :rtype: ArchivedBook
        :raises NotFoundError: If the actor has no such project.
        """
        await self._library.open(actor, project_id)
        project = await self._uow.read_book(project_id, lambda repos: repos.projects.get(project_id))
        database = await self._uow.snapshot_book(project_id)
        return ArchivedBook(title=project.details.title, pieces=self._files.archive.export(project_id, database))

    async def import_archive(self, actor: Actor, archive: BinaryIO) -> ProjectOverview:
        """Take the archive of a book in as a new book of the actor, under a new identifier.

        The archive is unpacked database first, then the database is brought to this release and its project renamed,
        and the entry of the library comes last, as for a book created here. A failure removes what was unpacked, and an
        import broken off is a store without an entry, which the start pass removes.

        :param actor: Account acting in the current request, which becomes the owner.
        :type actor: Actor
        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :returns: The new project with the counts and the progress of its book.
        :rtype: ProjectOverview
        :raises InvalidArchiveError: If the file is not the archive of a book.
        :raises BookTooNewError: If the book was exported by a newer release.
        """
        project_id = ProjectId(uuid4())
        try:
            await self._files.archive.unpack(project_id, archive)
            await self._uow.adopt_book(project_id)
        except Exception:
            await self._erase(project_id)
            raise
        entry = LibraryEntry(project_id=project_id, owner_id=actor.account_id, added_at=self._clock.now())
        await self._uow.change(lambda repos: repos.library.add(entry))
        return await self._overview(project_id)

    async def _overview(self, project_id: ProjectId) -> ProjectOverview:
        """Read one project with the counts and the progress of its book.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The project with the counts and the progress of its book.
        :rtype: ProjectOverview
        """
        return await self._uow.read_book(
            project_id,
            lambda repos: self._stages.with_progress(repos, repos.projects.overview(repos.projects.get(project_id))),
        )

    async def _erase(self, project_id: ProjectId) -> None:
        """Delete the source files and derived files of a book, then its store, which a repeated call finishes.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        await self._files.sources.delete_project(project_id)
        await self._files.assets.delete_project(project_id)
        await self._uow.delete_book(project_id)

    async def _forget(self, project_id: ProjectId) -> None:
        """Delete the entry of a book from the library.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        await self._uow.change(lambda repos: repos.library.delete(project_id))

    async def delete(self, actor: Actor, project_id: ProjectId) -> None:
        """Delete the project's source files and derived files, then its store, then its entry in the library.

        The owner is checked before any file goes, and the files go outside any transaction, so a slow store holds no
        lock of the book. A failure at any step leaves the entry in place, and calling this again finishes the
        deletion.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :raises NotFoundError: If the actor has no such project.
        """
        await self._library.open(actor, project_id)
        await self._erase(project_id)
        # The entry goes last: only an existing entry lets a repeated call reach what a failed call left behind
        await self._forget(project_id)

    async def delete_all(self, actor: Actor) -> None:
        """Delete every project of the actor with all its files, which must happen before its account is deleted.

        Each project is deleted in transactions of its own, files first, so a failure keeps the projects not yet
        deleted, and calling this again deletes them. The first window of the actor's projects is read again after
        every window, since the deleted projects leave it.

        :param actor: Account whose projects are deleted.
        :type actor: Actor
        """
        while True:
            window = await self._uow.read(lambda repos: repos.library.list_for_owner(actor.account_id, SliceRequest()))
            if not window.items:
                return
            for entry in window.items:
                await self.delete(actor, entry.project_id)

    async def sweep(self) -> None:
        """Finish the creations and deletions of books that were broken off half way, which is a pass of the start.

        A creation stores the book before its entry, and a deletion removes the entry after the book, so a store
        without an entry is a creation that stopped and an entry without a store is a deletion that stopped. No one can
        open the book of either, so its files and what is left of it go.
        """
        stored = set(await self._uow.stored_books())
        entries = {entry.project_id for entry in await self._uow.read(lambda repos: repos.library.list_all())}
        for project_id in sorted(stored - entries):
            await self._erase(project_id)
        for project_id in sorted(entries - stored):
            await self._erase(project_id)
            await self._forget(project_id)
