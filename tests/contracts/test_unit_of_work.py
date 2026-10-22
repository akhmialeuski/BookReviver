"""Contract of the calls of the unit of work, run against the SQL adapter, the application's own.

``change_book`` and ``change`` run their work on one transaction of one store, commit it when the work returns, roll it
back when the work raises, and wait for any other change of the same store: the same book, or the catalogue. A change of
one book never waits for a change of another book or of the catalogue. ``read_book`` and ``read`` run their work on one
committed state and refuse every write. ``create_book``, ``delete_book`` and ``stored_books`` make, remove and list the
books. Concurrency is real: tasks of an ``anyio`` task group, ordered by a held store, by events and by
``anyio.wait_all_tasks_blocked`` and never by a sleep.
"""

from datetime import timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.app.settings import ChangeWaitSettings
from bookreviver.domain.entities import LibraryEntry
from bookreviver.domain.enums import ImagePolicy, Stage, ValueScope
from bookreviver.domain.errors import BookBusyError, ConflictError, DomainError, NotFoundError
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.values import PageStepKey, SliceRequest
from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories, NoChangeOpenError, UnitOfWork
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.builders import (
    EPOCH,
    make_book_place,
    make_page,
    make_page_history_entry,
    make_page_stage,
    make_page_step_state,
    make_page_version,
    make_project,
    make_recipe,
    make_recipe_profile,
    make_scan,
    make_section,
    make_source,
    make_step_values,
)
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import AccountId
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# The limit of a unit of work of a request, short enough for a test of a refused wait to take a fraction of a second
REQUEST_WAIT_SECONDS: float = 0.2
# The limit of every other unit of work, long enough that a wait the test ends by itself never reaches it
JOB_WAIT_SECONDS: float = 5.0
# What a refusal under the limit of a request must be quicker than, well over that limit and well under the limit of a
# job, so that a unit of work given the limit of a job fails the test
REFUSAL_BOUND_SECONDS: float = 2.0
ONE_MINUTE: timedelta = timedelta(minutes=1)
LEFT_THE_WORK: str = 'left the work'
RENAMED: str = 'Renamed'
STAMP: str = 'Stamp'
FIRST_LABEL: str = '1'
# The steps of the tests of the order in which waiting changes run
SECOND_IN: str = 'second in'
REQUEST_GAVE_UP: str = 'request gave up'
HOLDER_RELEASED: str = 'holder released'
OTHER_BOOK: str = 'other book'
CATALOGUE: str = 'catalogue'
# How many changes queue behind a held store in the test of the order they run in
WAITERS: int = 4
# A window of the library or of a book that holds every row the tests make
SLICE_ALL: SliceRequest = SliceRequest(limit=100)
KIND_ARG: str = 'kind'
ERROR_ARG: str = 'error_type'
# A programming mistake and a rule of the domain, which a change treats alike
ERROR_TYPES: list[type[Exception]] = [DomainError, ValueError]
# Every method of a repository that writes
WRITE_METHODS: tuple[str, ...] = (
    'add',
    'add_many',
    'update',
    'update_many',
    'delete',
    'delete_many',
    'delete_for_step',
    'save',
    'save_many',
)


class Book(NamedTuple):
    """A stored book and the account that owns it.

    :ivar project: The project of the book.
    :ivar owner_id: The account that owns the book.
    """

    project: Project
    owner_id: AccountId


class ChangeKind(NamedTuple):
    """A kind of change of the port, which the tests of what every change promises run each of.

    A change of a book writes rows of its book, and a change of the catalogue writes rows of the catalogue, so each kind
    knows two rows of its store that are written together, and how to list the ones stored.

    :ivar of_book: Whether the change is of a book, and not of the catalogue.
    """

    of_book: bool

    async def run[T](self, uow: UnitOfWork, book: Book, work: Callable[[Any], T]) -> T:
        """Run a work over the repositories of the store as a change of this kind.

        :param uow: Unit of work to run the work on.
        :type uow: UnitOfWork
        :param book: The book of the test.
        :type book: Book
        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Any], T]
        :returns: What the work returns.
        :rtype: T
        """
        if self.of_book:
            return await uow.change_book(book.project.id, lambda repos, _project: work(repos))
        return await uow.change(work)

    async def read[T](self, uow: UnitOfWork, book: Book, work: Callable[[Any], T]) -> T:
        """Run a work over the repositories of the store as a read of this kind.

        :param uow: Unit of work to run the work on.
        :type uow: UnitOfWork
        :param book: The book of the test.
        :type book: Book
        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Any], T]
        :returns: What the work returns.
        :rtype: T
        """
        if self.of_book:
            return await uow.read_book(book.project.id, work)
        return await uow.read(work)

    def lock_of(self, book: Book) -> ProjectId | None:
        """Name what a holder holds to stop a change of this kind.

        :param book: The book of the test.
        :type book: Book
        :returns: The project whose book the change takes, or None for a change of the catalogue.
        :rtype: ProjectId | None
        """
        return book.project.id if self.of_book else None

    def rows(self, book: Book, number: int) -> tuple[Any, Any]:
        """Build two rows of two repositories of the store, which differ from those of any other number.

        :param book: The book of the test.
        :type book: Book
        :param number: Which pair of rows to build.
        :type number: int
        :returns: A recipe and a source of the book, or a recipe profile and a library entry of the owner.
        :rtype: tuple[Any, Any]
        """
        if self.of_book:
            return (
                make_recipe(stage=list(Stage)[number]),
                make_source(name=f'{number}.pdf'),
            )
        return (
            make_recipe_profile(account_id=book.owner_id, minutes=number),
            LibraryEntry(
                project_id=ProjectId(uuid4()), owner_id=book.owner_id, added_at=EPOCH + timedelta(minutes=number)
            ),
        )

    def add(self, repos: Any, rows: tuple[Any, Any]) -> None:
        """Add a pair of rows through the repositories of the store.

        :param repos: Repositories of the transaction.
        :type repos: Any
        :param rows: The pair that ``rows`` built.
        :type rows: tuple[Any, Any]
        """
        first, second = rows
        if self.of_book:
            repos.recipes.add(first)
            repos.sources.add(second)
        else:
            repos.recipe_profiles.add(first)
            repos.library.add(second)

    def found(self, repos: Any, book: Book) -> set[Any]:
        """List the identifiers of the rows of the store that ``rows`` builds and the book or its owner has.

        The recipes and the sources of the book count, and the profiles and the library entries of the owner do.

        :param repos: Repositories of the transaction.
        :type repos: Any
        :param book: The book of the test.
        :type book: Book
        :returns: The identifiers of the rows, a recipe and a source apart from the book's own, if any.
        :rtype: set[Any]
        """
        if self.of_book:
            return {recipe.id for recipe in repos.recipes.list_all()} | {
                source.id for source in repos.sources.list_all()
            }
        listed = repos.library.list_for_owner(book.owner_id, SLICE_ALL).items
        return {profile.id for profile in repos.recipe_profiles.list_for_account(book.owner_id)} | {
            entry.project_id for entry in listed
        }


type Run[T] = Callable[[UnitOfWork, Book, Callable[[Any], T]], Coroutine[Any, Any, T]]

KINDS: dict[str, ChangeKind] = {'change_book': ChangeKind(of_book=True), 'change': ChangeKind(of_book=False)}
KIND_PARAMS: list[ChangeKind] = list(KINDS.values())
KIND_IDS: list[str] = list(KINDS)


class Row(NamedTuple):
    """A row of one repository that is stored, a changed copy of it, and a row that is not stored.

    :ivar stored: The stored entity.
    :ivar changed: A copy of the stored entity with a field changed, so that writing it changes the row.
    :ivar fresh: An entity that is not stored.
    :ivar key: What ``delete`` takes to name the stored entity.
    """

    stored: Any
    changed: Any
    fresh: Any
    key: Any


# How each write method is called on a repository, given the rows of the test
WRITES: dict[str, Callable[[Any, Row], object]] = {
    'add': lambda repository, row: repository.add(row.fresh),
    'add_many': lambda repository, row: repository.add_many([row.fresh]),
    'update': lambda repository, row: repository.update(row.changed),
    'update_many': lambda repository, row: repository.update_many([row.changed]),
    'delete': lambda repository, row: repository.delete(row.key),
    'delete_many': lambda repository, row: repository.delete_many([row.key]),
    'delete_for_step': lambda repository, row: repository.delete_for_step(
        PageStepKey(page_id=row.stored.page_id, stage=row.stored.stage, step_id=row.stored.step_id)
    ),
    'save': lambda repository, row: repository.save(row.changed),
    'save_many': lambda repository, row: repository.save_many([row.changed]),
}


class WriteCase(NamedTuple):
    """A write method of a repository of the port, and the kind of store the repository is in.

    :ivar repository: Name of the repository of the port.
    :ivar method: Name of the write method of the repository.
    :ivar kind: The kind of store, a book or the catalogue, which holds the repository.
    """

    repository: str
    method: str
    kind: ChangeKind


# Each repository of the port with each write method it has, which the port's own declaration lists
WRITE_PARAMS: list[Any] = [
    pytest.param(WriteCase(repository, method, kind), id=f'{repository}.{method}')
    for kind, repositories in ((KINDS['change_book'], BookRepositories), (KINDS['change'], CatalogueRepositories))
    for repository, repository_type in repositories.__annotations__.items()
    for method in WRITE_METHODS
    if hasattr(repository_type, method)
]


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Shorten the wait limit of a request, so a refused wait takes a fraction of a second.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: The same settings with the wait limits of the suite.
    :rtype: Settings
    """
    limits = ChangeWaitSettings(request_seconds=REQUEST_WAIT_SECONDS, job_seconds=JOB_WAIT_SECONDS)
    return fx_settings.model_copy(update={'change_wait': limits})


async def _store_book(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> Book:
    """Store a book of a new account.

    :param uow_factory: Function opening a new unit of work over the catalogue and the books of the test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the catalogue accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The stored book.
    :rtype: Book
    """
    project, owner_id = make_project(), await new_owner()
    await store_project(uow_factory(), project, owner=owner_id)
    return Book(project=project, owner_id=owner_id)


@pytest.fixture
async def fx_book(fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> Book:
    """Store a book that the changes of the tests change.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
    :type fx_new_owner: OwnerFactory
    :returns: The stored book.
    :rtype: Book
    """
    return await _store_book(fx_uow, fx_new_owner)


@pytest.fixture
async def fx_other_book(fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> Book:
    """Store a second book, which a change of the first book must not wait for.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
    :type fx_new_owner: OwnerFactory
    :returns: The stored book.
    :rtype: Book
    """
    return await _store_book(fx_uow, fx_new_owner)


@pytest.fixture
async def fx_book_rows(fx_uow: UnitOfWorkFactory, fx_book: Book) -> dict[str, Row]:
    """Store one row in every repository that holds the content of a book, and build a changed copy and a new row.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_book: Stored book, which gets the rows.
    :type fx_book: Book
    :returns: The rows by the name of the repository that holds them.
    :rtype: dict[str, Row]
    """
    project = fx_book.project
    source = make_source()
    scan = make_scan(source=source, number=0)
    page = make_page(scan=scan)
    section = make_section(page=page)
    version = make_page_version(page_id=page.id)
    stage = make_page_stage(page_id=page.id, stage=Stage.GEOMETRY)
    state = make_page_step_state(page_id=page.id)
    values = make_step_values()
    history_entry = make_page_history_entry(page_id=page.id)
    recipe = make_recipe()
    place = make_book_place()

    def add_rows(repos: BookRepositories, _project: Project) -> None:
        """Add one row to every repository that holds the content of the book.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.sources.add(source)
        repos.scans.add(scan)
        repos.pages.add(page)
        repos.pagination_sections.add(section)
        repos.page_versions.add(version)
        repos.page_stages.add(stage)
        repos.page_step_states.add(state)
        repos.step_values.add(values)
        repos.page_history.add(history_entry)
        repos.recipes.add(recipe)
        repos.book_places.save(place)

    await fx_uow().change_book(project.id, add_rows)
    return {
        'projects': Row(
            project,
            evolve(project, updated_at=project.updated_at + ONE_MINUTE),
            make_project(title='Another'),
            project.id,
        ),
        'sources': Row(
            source,
            evolve(source, imported_at=source.imported_at + ONE_MINUTE),
            make_source(name='other.pdf'),
            source.id,
        ),
        'scans': Row(scan, evolve(scan, source_label=RENAMED), make_scan(source=source, number=1), scan.id),
        'pages': Row(
            page,
            evolve(page, updated_at=page.updated_at + ONE_MINUTE),
            make_page(order_key='a1'),
            page.id,
        ),
        'pagination_sections': Row(
            section, evolve(section, name=RENAMED), make_section(page=page, minutes=1), section.id
        ),
        'page_versions': Row(
            version, evolve(version, comment='Checked'), make_page_version(page_id=page.id, minutes=1), version.id
        ),
        'page_stages': Row(stage, evolve(stage, through_step=1), evolve(stage, stage=Stage.CLEANUP), stage.key),
        'page_step_states': Row(
            state,
            evolve(state, updated_at=state.updated_at + ONE_MINUTE),
            evolve(state, stage=Stage.CLEANUP),
            state.key,
        ),
        'step_values': Row(
            values,
            evolve(values, updated_at=values.updated_at + ONE_MINUTE),
            evolve(values, scope=ValueScope.ODD),
            values.key,
        ),
        'page_history': Row(
            history_entry,
            evolve(history_entry, created_at=history_entry.created_at + ONE_MINUTE),
            make_page_history_entry(page_id=page.id),
            history_entry.id,
        ),
        'recipes': Row(
            recipe,
            evolve(recipe, updated_at=recipe.updated_at + ONE_MINUTE),
            make_recipe(stage=Stage.CLEANUP),
            recipe.id,
        ),
        'book_places': Row(
            place,
            evolve(place, updated_at=place.updated_at + ONE_MINUTE),
            make_book_place(stage=Stage.CLEANUP),
            None,
        ),
    }


@pytest.fixture
async def fx_rows(fx_uow: UnitOfWorkFactory, fx_book: Book, fx_book_rows: dict[str, Row]) -> dict[str, Row]:
    """Store one row in every repository of the catalogue too, and build a changed copy and a new row beside each.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_book: Stored book, whose library entry is the row of the library.
    :type fx_book: Book
    :param fx_book_rows: Rows of the repositories that hold the content of the book.
    :type fx_book_rows: dict[str, Row]
    :returns: The rows by the name of the repository that holds them, for every repository of the port.
    :rtype: dict[str, Row]
    """
    owner_id = fx_book.owner_id
    profile = make_recipe_profile(account_id=owner_id)
    entry = await fx_uow().read(lambda repos: repos.library.get(fx_book.project.id))
    await fx_uow().change(lambda repos: repos.recipe_profiles.add(profile))
    return {
        **fx_book_rows,
        'library': Row(
            entry,
            evolve(entry, added_at=entry.added_at + ONE_MINUTE),
            LibraryEntry(project_id=ProjectId(uuid4()), owner_id=owner_id, added_at=entry.added_at),
            entry.project_id,
        ),
        'recipe_profiles': Row(
            profile,
            evolve(profile, updated_at=profile.updated_at + ONE_MINUTE),
            make_recipe_profile(account_id=owner_id, stage=Stage.CLEANUP),
            profile.id,
        ),
    }


class TestChangeEnd:
    """Contract of how the work of a change ends: it commits when it returns and rolls back when it raises."""

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_commits_when_the_work_returns(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book, kind: ChangeKind
    ) -> None:
        """Verify what the work wrote is visible to another unit of work once the change has returned.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the change belongs to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        rows = kind.rows(fx_book, 1)
        await kind.run(fx_uow(), fx_book, lambda repos: kind.add(repos, rows))
        found = await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book))
        assert {row.id for row in rows} <= found

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_returns_what_the_work_returns(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book, kind: ChangeKind
    ) -> None:
        """Verify the change hands back the value of its work, which is what a use case reads as its result.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the change belongs to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        rows = kind.rows(fx_book, 1)

        def add_and_count(repos: Any) -> tuple[tuple[Any, Any], int]:
            """Add the rows and count the rows of the store, which then include them.

            :param repos: Repositories of the transaction.
            :type repos: Any
            :returns: The rows, and how many rows of the store there are.
            :rtype: tuple[tuple[Any, Any], int]
            """
            kind.add(repos, rows)
            return rows, len(kind.found(repos, fx_book))

        before = await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book))
        returned = await kind.run(fx_uow(), fx_book, add_and_count)
        assert returned == (rows, len(before) + len(rows))

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    @pytest.mark.parametrize(ERROR_ARG, ERROR_TYPES, ids=[error.__name__ for error in ERROR_TYPES])
    async def test_change_rolls_back_everything_and_reraises_the_exception(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book, kind: ChangeKind, error_type: type[Exception]
    ) -> None:
        """Verify an exception raised by the work discards every write of the work and reaches the caller as it is.

        The work writes in two repositories before it raises. The same unit of work then writes the same rows in a new
        change, which proves that the store is free again and that nothing of the first change was kept.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the change belongs to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        :param error_type: Class of the exception the work raises.
        :type error_type: type[Exception]
        """
        rows = kind.rows(fx_book, 1)
        wanted = {row.id for row in rows}
        error = error_type(LEFT_THE_WORK)
        uow = fx_uow()
        before = await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book))

        def write_and_raise(repos: Any) -> None:
            """Add two rows of two repositories, and raise the exception of the test.

            :param repos: Repositories of the transaction.
            :type repos: Any
            :raises Exception: Always, the exception of the test.
            """
            kind.add(repos, rows)
            raise error

        with pytest.raises(error_type, match=LEFT_THE_WORK) as raised:
            await kind.run(uow, fx_book, write_and_raise)
        expect(raised.value is error)
        expect(await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book)) == before)
        await kind.run(uow, fx_book, lambda repos: kind.add(repos, rows))
        expect(await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book)) == before | wanted)
        assert_expectations()


class TestChangeBook:
    """Contract of UnitOfWork.change_book() on the project it hands its work."""

    async def test_work_receives_the_stored_project(self, fx_uow: UnitOfWorkFactory, fx_book: Book) -> None:
        """Verify the work is handed the project as stored, beside the repos.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the change belongs to.
        :type fx_book: Book
        """
        received = await fx_uow().change_book(fx_book.project.id, lambda _repos, project: project)
        assert received == fx_book.project

    async def test_missing_book_raises_not_found_and_runs_no_work(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a change of a book that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        missing = make_project()
        ran: list[bool] = []
        uow = fx_uow()
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await uow.change_book(missing.id, lambda _repos, _project: ran.append(True))
        assert ran == []

    async def test_work_receives_the_project_as_the_previous_change_left_it(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book
    ) -> None:
        """Verify a change reads the project once the change before it has committed, not as it was stored first.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book both changes belong to.
        :type fx_book: Book
        """
        changed = evolve(fx_book.project, image_policy=ImagePolicy.LOSSLESS)
        first, second = fx_uow(), fx_uow()
        await first.change_book(fx_book.project.id, lambda repos, _project: repos.projects.update(changed))
        received = await second.change_book(fx_book.project.id, lambda _repos, project: project)
        assert received == changed


class TestBookLifecycle:
    """Contract of create_book, delete_book and stored_books, which make, remove and list the books."""

    async def test_create_book_stores_the_project_and_returns_it(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a created book holds its project, which a later unit of work reads back unchanged.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project = make_project()
        created = await fx_uow().create_book(project)
        read = await fx_uow().read_book(project.id, lambda repos: repos.projects.get(project.id))
        expect(created == project)
        expect(read == project)
        assert_expectations()

    async def test_creating_a_stored_book_again_raises_conflict_and_keeps_the_first(
        self, fx_uow: UnitOfWorkFactory
    ) -> None:
        """Verify a second creation under the identifier of a stored book is a ConflictError, never an overwrite.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project = make_project(title='First')
        await fx_uow().create_book(project)
        duplicate = evolve(project, details=evolve(project.details, title='Second'))
        with pytest.raises(ConflictError, match=str(project.id)):
            await fx_uow().create_book(duplicate)
        assert await fx_uow().read_book(project.id, lambda repos: repos.projects.get(project.id)) == project

    async def test_deleted_book_answers_not_found_and_other_books_stay(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify nothing of a deleted book can be read or changed any more, and another book is left as it was.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        doomed, kept = make_project(), make_project()

        async def book_with_a_page(project: Project) -> None:
            """Make the book of a project and store one page in it.

            :param project: The project of the book.
            :type project: Project
            """
            await fx_uow().create_book(project)
            await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.add(make_page()))

        for project in (doomed, kept):
            await book_with_a_page(project)

        await fx_uow().delete_book(doomed.id)

        with pytest.raises(NotFoundError, match=str(doomed.id)):
            await fx_uow().read_book(doomed.id, lambda repos: repos.projects.get(doomed.id))
        with pytest.raises(NotFoundError, match=str(doomed.id)):
            await fx_uow().change_book(doomed.id, lambda _repos, _project: None)
        remaining = await fx_uow().read_book(kept.id, lambda repos: repos.pages.list_window(SLICE_ALL))
        assert remaining.total == 1

    async def test_deleting_a_book_again_or_one_never_stored_does_nothing(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a deletion broken off half way can run again, and a book that does not exist is left as it is.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project = make_project()
        await fx_uow().create_book(project)
        await fx_uow().delete_book(project.id)

        await fx_uow().delete_book(project.id)
        await fx_uow().delete_book(make_project().id)

        assert list(await fx_uow().stored_books()) == []

    async def test_a_deleted_book_can_be_created_again(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify the identifier of a deleted book is free, and the new book starts empty.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project = make_project()
        await fx_uow().create_book(project)
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.add(make_page()))
        await fx_uow().delete_book(project.id)

        await fx_uow().create_book(project)

        pages = await fx_uow().read_book(project.id, lambda repos: repos.pages.list_window(SLICE_ALL))
        assert pages.total == 0

    async def test_stored_books_lists_the_books_that_have_a_store(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify the stored books are those created and not deleted, whether the library lists them or not.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        first, second, third = make_project(), make_project(), make_project()
        before = list(await fx_uow().stored_books())
        for project in (first, second, third):
            await fx_uow().create_book(project)
        await fx_uow().delete_book(second.id)
        assert (before, set(await fx_uow().stored_books())) == ([], {first.id, third.id})

    async def test_read_of_a_missing_book_raises_not_found_and_runs_no_work(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a read of a book that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        missing = make_project()
        ran: list[bool] = []
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await fx_uow().read_book(missing.id, lambda _repos: ran.append(True))
        assert ran == []


class TestRead:
    """Contract of UnitOfWork.read() and read_book() on what they return and what they see."""

    async def test_read_book_returns_what_the_work_returns(self, fx_uow: UnitOfWorkFactory, fx_book: Book) -> None:
        """Verify ``read_book`` hands back the value of its work.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book that is read.
        :type fx_book: Book
        """
        project = fx_book.project
        read = await fx_uow().read_book(project.id, lambda repos: repos.projects.get(project.id))
        assert read == project

    async def test_read_returns_what_the_work_returns(self, fx_uow: UnitOfWorkFactory, fx_book: Book) -> None:
        """Verify ``read`` hands back the value of its work.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book whose library entry is read.
        :type fx_book: Book
        """
        entry = await fx_uow().read(lambda repos: repos.library.get(fx_book.project.id))
        assert (entry.project_id, entry.owner_id) == (fx_book.project.id, fx_book.owner_id)

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_read_sees_every_write_of_a_committed_change_and_none_of_a_rolled_back_one(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book, kind: ChangeKind
    ) -> None:
        """Verify a read sees the two rows of a change that committed, and nothing of a change that rolled back.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the rows belong to.
        :type fx_book: Book
        :param kind: The kind of store under test.
        :type kind: ChangeKind
        """
        kept, lost = kind.rows(fx_book, 1), kind.rows(fx_book, 2)
        before = await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book))
        uow = fx_uow()

        def add_lost(repos: Any) -> None:
            """Add the other two rows and raise, so the change rolls back.

            :param repos: Repositories of the transaction.
            :type repos: Any
            :raises DomainError: Always.
            """
            kind.add(repos, lost)
            raise DomainError

        await kind.run(uow, fx_book, lambda repos: kind.add(repos, kept))
        with pytest.raises(DomainError):
            await kind.run(uow, fx_book, add_lost)
        found = await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book))
        assert found == before | {row.id for row in kept}


class TestChangeWait:
    """Contract of what a change waits for: any other change of the same store, for as long as the limit.

    A store is held by a work that blocks the thread it runs in. A book and the catalogue are stores of their own, each
    with one writer, so a change of one store never waits for a change of another.
    """

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_waits_until_the_store_is_free(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book: Book,
        kind: ChangeKind,
    ) -> None:
        """Verify a change does not run while another change holds its store, and runs once the store is free.

        The test lets every task block, and checks that the work has not run, before it frees the store.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the changes belong to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        holder, waiter = fx_uow(), fx_uow()
        ran: list[str] = []
        ran_while_held: list[str] = []

        async def wait_and_run() -> None:
            """Ask for the change and note when its work runs."""
            await kind.run(waiter, fx_book, lambda _repos: ran.append(SECOND_IN))

        async with anyio.create_task_group() as tasks, hold_book_in_thread(holder, kind.lock_of(fx_book)):
            tasks.start_soon(wait_and_run)
            await anyio.wait_all_tasks_blocked()
            ran_while_held.extend(ran)
        expect(ran_while_held == [])
        expect(ran == [SECOND_IN])
        assert_expectations()

    async def test_a_change_of_a_book_does_not_wait_for_a_change_of_another_book_or_of_the_catalogue(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book, fx_other_book: Book
    ) -> None:
        """Verify a book held by a change leaves a change of another book and a change of the catalogue free to run.

        The changes have the limit of a request, so one that wrongly waited for the held book would be refused with
        ``BookBusyError`` within a fraction of a second and fail the test, instead of passing for a slow one.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the holder holds.
        :type fx_book: Book
        :param fx_other_book: Stored book another change changes.
        :type fx_other_book: Book
        """
        holder, impatient = fx_uow(), fx_uow(wait_seconds=REQUEST_WAIT_SECONDS)
        ran: list[str] = []
        async with hold_book_in_thread(holder, fx_book.project.id):
            await impatient.change_book(fx_other_book.project.id, lambda _repos, _project: ran.append(OTHER_BOOK))
            await impatient.change(lambda _repos: ran.append(CATALOGUE))
            ran_while_held = list(ran)
        assert ran_while_held == [OTHER_BOOK, CATALOGUE]

    async def test_a_change_of_a_book_does_not_wait_for_a_change_of_the_catalogue(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book
    ) -> None:
        """Verify the catalogue held by a change leaves a change of a book free to run.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book that is changed.
        :type fx_book: Book
        """
        holder, impatient = fx_uow(), fx_uow(wait_seconds=REQUEST_WAIT_SECONDS)
        ran: list[str] = []
        async with hold_book_in_thread(holder, None):
            await impatient.change_book(fx_book.project.id, lambda _repos, _project: ran.append(OTHER_BOOK))
            ran_while_held = list(ran)
        assert ran_while_held == [OTHER_BOOK]

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_reads_what_the_changes_before_it_committed(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book: Book,
        kind: ChangeKind,
    ) -> None:
        """Verify two changes that queue behind a held store run one after the other, the second reading the first.

        Each change is started only once every task is blocked, so the first asked before the second.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the changes belong to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        first_rows, second_rows = kind.rows(fx_book, 1), kind.rows(fx_book, 2)
        holder, first, second = fx_uow(), fx_uow(), fx_uow()
        seen: list[bool] = []

        def add_second(repos: Any) -> None:
            """Note whether the rows of the first change are stored, and add the rows of the second.

            :param repos: Repositories of the transaction.
            :type repos: Any
            """
            seen.append({row.id for row in first_rows} <= kind.found(repos, fx_book))
            kind.add(repos, second_rows)

        async with anyio.create_task_group() as tasks, hold_book_in_thread(holder, kind.lock_of(fx_book)):
            tasks.start_soon(kind.run, first, fx_book, lambda repos: kind.add(repos, first_rows))
            await anyio.wait_all_tasks_blocked()
            tasks.start_soon(kind.run, second, fx_book, add_second)
            await anyio.wait_all_tasks_blocked()
        found = await kind.read(fx_uow(), fx_book, lambda repos: kind.found(repos, fx_book))
        expect(seen == [True])
        expect({row.id for row in (*first_rows, *second_rows)} <= found)
        assert_expectations()

    async def test_two_changes_of_a_book_that_read_and_write_one_page_keep_both_writes(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book
    ) -> None:
        """Verify two changes that each read a page and write another field of it both keep their writes.

        Both are started while the book is held, so each would write the page as it read it if the second were let in
        before the first had committed.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book both changes belong to.
        :type fx_book: Book
        """
        project_id = fx_book.project.id
        page = make_page()
        holder, first, second = fx_uow(), fx_uow(), fx_uow()
        await first.change_book(project_id, lambda repos, _project: repos.pages.add(page))

        def label_page(repos: BookRepositories, _project: Project) -> None:
            """Read the page and write its label.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.pages.update(evolve(repos.pages.get(page.id), manual_label=FIRST_LABEL))

        def note_page(repos: BookRepositories, _project: Project) -> None:
            """Read the page and write its notes.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.pages.update(evolve(repos.pages.get(page.id), notes=STAMP))

        async with anyio.create_task_group() as tasks, hold_book_in_thread(holder, project_id):
            tasks.start_soon(first.change_book, project_id, label_page)
            await anyio.wait_all_tasks_blocked()
            tasks.start_soon(second.change_book, project_id, note_page)
            await anyio.wait_all_tasks_blocked()
        stored = await fx_uow().read_book(project_id, lambda repos: repos.pages.get(page.id))
        assert (stored.manual_label, stored.notes) == (FIRST_LABEL, STAMP)

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_changes_waiting_for_one_holder_run_in_the_order_they_asked(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book: Book,
        kind: ChangeKind,
    ) -> None:
        """Verify changes that queue behind a held store run one by one in the order they asked, first come first in.

        Each waiter is started only once every task is blocked, so it asks after the one before it.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book every change belongs to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        holder = fx_uow()
        waiters = [fx_uow() for _ in range(WAITERS)]
        ran: list[int] = []

        async def wait_and_run(number: int) -> None:
            """Ask for the change and note the number of this waiter when its work runs.

            :param number: Position of this waiter in the order of asking.
            :type number: int
            """
            await kind.run(waiters[number], fx_book, lambda _repos: ran.append(number))

        async with anyio.create_task_group() as tasks, hold_book_in_thread(holder, kind.lock_of(fx_book)):
            for number in range(WAITERS):
                tasks.start_soon(wait_and_run, number)
                await anyio.wait_all_tasks_blocked()
        assert ran == list(range(WAITERS))

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_wait_longer_than_the_limit_raises_book_busy_and_runs_no_work(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book: Book,
        kind: ChangeKind,
    ) -> None:
        """Verify a change gives up with ``BookBusyError`` after the limit of its unit of work, and runs no work.

        The unit of work has the limit of a request, so the wait ends in a fraction of a second whatever the holder
        does. The refusal leaves the store of the holder as it was, and once the holder has let go, the same unit of
        work runs the change at once.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book the changes belong to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        holder, impatient = fx_uow(), fx_uow(wait_seconds=REQUEST_WAIT_SECONDS)
        ran: list[str] = []
        before = await kind.read(holder, fx_book, lambda repos: kind.found(repos, fx_book))
        async with hold_book_in_thread(holder, kind.lock_of(fx_book)):
            started = perf_counter()
            with pytest.raises(BookBusyError):
                await kind.run(impatient, fx_book, lambda _repos: ran.append(SECOND_IN))
            gave_up_after = perf_counter() - started
        ran_while_held = list(ran)
        kept = await kind.read(holder, fx_book, lambda repos: kind.found(repos, fx_book))
        await kind.run(impatient, fx_book, lambda _repos: ran.append(SECOND_IN))
        expect(gave_up_after < REFUSAL_BOUND_SECONDS)
        expect(ran_while_held == [])
        expect(kept == before, 'the refusal left the store of the holder as it was')
        expect(ran == [SECOND_IN])
        assert_expectations()

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_wait_longer_than_the_limit_of_a_request_enters_under_the_limit_of_a_job(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book: Book,
        kind: ChangeKind,
    ) -> None:
        """Verify a unit of work with the limit of a job waits for the holder past the limit of a request.

        A unit of work with the limit of a request gives up while the holder still holds the store, which proves that
        more than that limit has passed, and only then is the holder let go. The unit of work of the job asked before
        the request one, so it has waited at least as long, and runs after the holder instead of raising
        ``BookBusyError``.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book every change belongs to.
        :type fx_book: Book
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        holder, job, request = (
            fx_uow(),
            fx_uow(),
            fx_uow(wait_seconds=REQUEST_WAIT_SECONDS),
        )
        order: list[str] = []

        async def wait_as_a_job() -> None:
            """Ask for the change under the limit of a job and note when its work runs."""
            await kind.run(job, fx_book, lambda _repos: order.append(SECOND_IN))

        async with anyio.create_task_group() as tasks, hold_book_in_thread(holder, kind.lock_of(fx_book)):
            tasks.start_soon(wait_as_a_job)
            await anyio.wait_all_tasks_blocked()
            with pytest.raises(BookBusyError):
                await kind.run(request, fx_book, lambda _repos: order.append(REQUEST_GAVE_UP))
            order.append(HOLDER_RELEASED)
        assert order == [HOLDER_RELEASED, SECOND_IN]


class TestWriteGuard:
    """Contract of NoChangeOpenError: no repository writes in the work of a read."""

    async def test_every_repository_has_its_rows(self, fx_rows: dict[str, Row]) -> None:
        """Verify the rows of the guard test cover every repository the port declares.

        :param fx_rows: Rows by the name of the repository that holds them.
        :type fx_rows: dict[str, Row]
        """
        declared = set(BookRepositories.__annotations__) | set(CatalogueRepositories.__annotations__)
        assert set(fx_rows) == declared

    @pytest.mark.parametrize('case', WRITE_PARAMS)
    async def test_write_in_a_read_raises(
        self, fx_uow: UnitOfWorkFactory, fx_book: Book, fx_rows: dict[str, Row], case: WriteCase
    ) -> None:
        """Verify a repository refuses a write in the work of ``read`` or ``read_book``.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Stored book whose rows are written.
        :type fx_book: Book
        :param fx_rows: Rows by the name of the repository that holds them.
        :type fx_rows: dict[str, Row]
        :param case: The write method of a repository, and the kind of store the repository is in.
        :type case: WriteCase
        """

        def write(repos: Any) -> object:
            """Call the write method on the repository, which has to refuse it.

            :param repos: Repositories of the transaction.
            :type repos: Any
            :returns: What the write returns, if it was not refused.
            :rtype: object
            """
            return WRITES[case.method](getattr(repos, case.repository), fx_rows[case.repository])

        with pytest.raises(NoChangeOpenError):
            await case.kind.read(fx_uow(), fx_book, write)
