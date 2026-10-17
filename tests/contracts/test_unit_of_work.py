"""Contract of the calls of the unit of work, run against the SQL adapter, the application's own.

``change_book`` and ``change`` run their work on one transaction, commit it when the work returns, roll it back when the
work raises, and wait for any other change of the same book. ``read`` runs its work on one committed state and refuses
every write. Concurrency is real: tasks of an ``anyio`` task group, ordered by a held book, by events and by
``anyio.wait_all_tasks_blocked`` and never by a sleep. SQLite has one writer, so a held change holds every other change
of every book.
"""

from datetime import timedelta
from time import perf_counter
from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.app.settings import ChangeWaitSettings
from bookreviver.domain.enums import ImagePolicy, Stage, ValueScope
from bookreviver.domain.errors import BookBusyError, DomainError, NotFoundError
from bookreviver.domain.values import PageStepKey
from bookreviver.ports.persistence import NoChangeOpenError, Repositories, UnitOfWork
from tests.helpers.builders import (
    make_book_place,
    make_page,
    make_page_stage,
    make_page_step_change,
    make_page_step_state,
    make_page_version,
    make_project,
    make_recipe,
    make_recipe_profile,
    make_result_mark_change,
    make_scan,
    make_section,
    make_source,
    make_step_values,
)
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project, RecipeProfile
    from bookreviver.domain.ids import AccountId, ProjectId
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import BookHolder, UnitOfWorkFactory

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
# How many changes queue behind a held book in the test of the order they run in
WAITERS: int = 4
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

type Run[T] = Callable[[UnitOfWork, Project, Callable[[Repositories], T]], Coroutine[Any, Any, T]]


async def run_change_book[T](uow: UnitOfWork, project: Project, work: Callable[[Repositories], T]) -> T:
    """Run a work that needs only the repositories as a change of the book of the project.

    :param uow: Unit of work to run the work on.
    :type uow: UnitOfWork
    :param project: Project whose book is changed.
    :type project: Project
    :param work: Function over the repositories of the transaction.
    :type work: Callable[[Repositories], T]
    :returns: What the work returns.
    :rtype: T
    """
    return await uow.change_book(project.id, lambda repos, _project: work(repos))


async def run_change[T](uow: UnitOfWork, _project: Project, work: Callable[[Repositories], T]) -> T:
    """Run a work that needs only the repositories as a change outside the content of a book.

    :param uow: Unit of work to run the work on.
    :type uow: UnitOfWork
    :param _project: Project of the test, which this change does not need.
    :type _project: Project
    :param work: Function over the repositories of the transaction.
    :type work: Callable[[Repositories], T]
    :returns: What the work returns.
    :rtype: T
    """
    return await uow.change(work)


class ChangeKind(NamedTuple):
    """A kind of change of the port, which the tests of what every change promises run each of.

    :ivar run: Runs a work that needs only the repositories as a change of this kind.
    :ivar of_book: Whether the change takes the lock of a book, and not the lock of the changes outside books.
    """

    run: Run[Any]
    of_book: bool

    def lock_of(self, project: Project) -> ProjectId | None:
        """Name what a book holder holds to stop a change of this kind.

        :param project: Project of the test.
        :type project: Project
        :returns: The project whose book the change takes, or None for the changes outside the content of a book.
        :rtype: ProjectId | None
        """
        return project.id if self.of_book else None


KINDS: dict[str, ChangeKind] = {
    'change_book': ChangeKind(run_change_book, of_book=True),
    'change': ChangeKind(run_change, of_book=False),
}
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
# Each repository of the port with each write method it has, which the port's own declaration lists
WRITE_PARAMS: list[Any] = [
    pytest.param(repository, method, id=f'{repository}.{method}')
    for repository, repository_type in Repositories.__annotations__.items()
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


@pytest.fixture
async def fx_project(fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> Project:
    """Store a project that the changes of the tests change.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_new_owner: Function creating an account the backend accepts as an owner.
    :type fx_new_owner: OwnerFactory
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=await fx_new_owner())
    await store_project(fx_uow(), project)
    return project


@pytest.fixture
async def fx_book_rows(fx_uow: UnitOfWorkFactory, fx_project: Project) -> dict[str, Row]:
    """Store one row in every repository that holds the content of a book, and build a changed copy and a new row.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_project: Stored project, whose book gets the rows.
    :type fx_project: Project
    :returns: The rows by the name of the repository that holds them.
    :rtype: dict[str, Row]
    """
    project = fx_project
    source = make_source(project_id=project.id)
    scan = make_scan(source=source, number=0)
    page = make_page(project_id=project.id, scan=scan)
    section = make_section(page=page)
    version = make_page_version(page_id=page.id)
    stage = make_page_stage(page_id=page.id, stage=Stage.GEOMETRY)
    state = make_page_step_state(page_id=page.id)
    values = make_step_values(project_id=project.id)
    step_change = make_page_step_change(page_id=page.id)
    mark_change = make_result_mark_change(version_id=version.id)
    recipe = make_recipe(project_id=project.id)

    def add_rows(repos: Repositories, _project: Project) -> None:
        """Add one row to every repository that holds the content of the book.

        :param repos: Repositories of the transaction.
        :type repos: Repositories
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
        repos.page_step_changes.add(step_change)
        repos.result_mark_changes.add(mark_change)
        repos.recipes.add(recipe)

    await fx_uow().change_book(project.id, add_rows)
    return {
        'sources': Row(
            source,
            evolve(source, imported_at=source.imported_at + ONE_MINUTE),
            make_source(project_id=project.id, name='other.pdf'),
            source.id,
        ),
        'scans': Row(scan, evolve(scan, source_label=RENAMED), make_scan(source=source, number=1), scan.id),
        'pages': Row(
            page,
            evolve(page, updated_at=page.updated_at + ONE_MINUTE),
            make_page(project_id=project.id, order_key='a1'),
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
        'page_step_changes': Row(
            step_change,
            evolve(step_change, created_at=step_change.created_at + ONE_MINUTE),
            make_page_step_change(page_id=page.id),
            step_change.id,
        ),
        'result_mark_changes': Row(
            mark_change,
            evolve(mark_change, created_at=mark_change.created_at + ONE_MINUTE),
            make_result_mark_change(version_id=version.id),
            mark_change.id,
        ),
        'recipes': Row(
            recipe,
            evolve(recipe, updated_at=recipe.updated_at + ONE_MINUTE),
            make_recipe(project_id=project.id, stage=Stage.CLEANUP),
            recipe.id,
        ),
    }


@pytest.fixture
async def fx_rows(fx_uow: UnitOfWorkFactory, fx_project: Project, fx_book_rows: dict[str, Row]) -> dict[str, Row]:
    """Store one row in every other repository too, and build a changed copy and a new row beside each.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_project: Stored project, which owns the places.
    :type fx_project: Project
    :param fx_book_rows: Rows of the repositories that hold the content of the book.
    :type fx_book_rows: dict[str, Row]
    :returns: The rows by the name of the repository that holds them, for every repository of the port.
    :rtype: dict[str, Row]
    """
    project, owner_id = fx_project, fx_project.owner_id
    profile = make_recipe_profile(account_id=owner_id)
    place = make_book_place(account_id=owner_id, project_id=project.id)

    def add_rows(repos: Repositories) -> None:
        """Add one row to every repository that holds rows outside the content of a book.

        :param repos: Repositories of the transaction.
        :type repos: Repositories
        """
        repos.recipe_profiles.add(profile)
        repos.book_places.add(place)

    await fx_uow().change(add_rows)
    return {
        **fx_book_rows,
        'projects': Row(
            project,
            evolve(project, updated_at=project.updated_at + ONE_MINUTE),
            make_project(owner_id=owner_id, title='Another'),
            project.id,
        ),
        'recipe_profiles': Row(
            profile,
            evolve(profile, updated_at=profile.updated_at + ONE_MINUTE),
            make_recipe_profile(account_id=owner_id, stage=Stage.CLEANUP),
            profile.id,
        ),
        'book_places': Row(
            place,
            evolve(place, updated_at=place.updated_at + ONE_MINUTE),
            make_book_place(account_id=owner_id, project_id=project.id, stage=Stage.CLEANUP),
            place.key,
        ),
    }


async def stored_profiles(uow: UnitOfWork, account_id: AccountId) -> list[RecipeProfile]:
    """Read every recipe profile of an account, as committed.

    :param uow: Unit of work to read with.
    :type uow: UnitOfWork
    :param account_id: Account owning the profiles.
    :type account_id: AccountId
    :returns: The profiles of every stage.
    :rtype: list[RecipeProfile]
    """
    return list(await uow.read(lambda repos: repos.recipe_profiles.list_for_account(account_id)))


class TestChangeEnd:
    """Contract of how the work of a change ends: it commits when it returns and rolls back when it raises."""

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_commits_when_the_work_returns(
        self, fx_uow: UnitOfWorkFactory, fx_project: Project, kind: ChangeKind
    ) -> None:
        """Verify what the work wrote is visible to another unit of work once the change has returned.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project the change belongs to.
        :type fx_project: Project
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        profile = make_recipe_profile(account_id=fx_project.owner_id)
        await kind.run(fx_uow(), fx_project, lambda repos: repos.recipe_profiles.add(profile))
        other = fx_uow()
        assert await other.read(lambda repos: repos.recipe_profiles.get(profile.id)) == profile

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_returns_what_the_work_returns(
        self, fx_uow: UnitOfWorkFactory, fx_project: Project, kind: ChangeKind
    ) -> None:
        """Verify the change hands back the value of its work, which is what a use case reads as its result.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project the change belongs to.
        :type fx_project: Project
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        profile = make_recipe_profile(account_id=fx_project.owner_id)

        def add_and_count(repos: Repositories) -> tuple[RecipeProfile, int]:
            """Add the profile and count the profiles of the account, which then include it.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The profile as stored, and how many profiles the account has.
            :rtype: tuple[RecipeProfile, int]
            """
            stored = repos.recipe_profiles.add(profile)
            return stored, len(repos.recipe_profiles.list_for_account(fx_project.owner_id))

        assert await kind.run(fx_uow(), fx_project, add_and_count) == (profile, 1)

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    @pytest.mark.parametrize(ERROR_ARG, ERROR_TYPES, ids=[error.__name__ for error in ERROR_TYPES])
    async def test_change_rolls_back_everything_and_reraises_the_exception(
        self, fx_uow: UnitOfWorkFactory, fx_project: Project, kind: ChangeKind, error_type: type[Exception]
    ) -> None:
        """Verify an exception raised by the work discards every write of the work and reaches the caller as it is.

        The work writes in two repositories before it raises. The same unit of work then writes the same rows in a new
        change, which proves that the book is free again and that nothing of the first change was kept.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project the change belongs to.
        :type fx_project: Project
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        :param error_type: Class of the exception the work raises.
        :type error_type: type[Exception]
        """
        place = make_book_place(account_id=fx_project.owner_id, project_id=fx_project.id)
        profile = make_recipe_profile(account_id=fx_project.owner_id)
        error = error_type(LEFT_THE_WORK)
        uow = fx_uow()

        def write_and_raise(repos: Repositories) -> None:
            """Add a place of the book and a recipe profile, and raise the exception of the test.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :raises Exception: Always, the exception of the test.
            """
            repos.book_places.add(place)
            repos.recipe_profiles.add(profile)
            raise error

        def add_both(repos: Repositories) -> None:
            """Add the place of the book and the recipe profile.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            repos.book_places.add(place)
            repos.recipe_profiles.add(profile)

        with pytest.raises(error_type, match=LEFT_THE_WORK) as raised:
            await kind.run(uow, fx_project, write_and_raise)
        expect(raised.value is error)
        expect(await fx_uow().read(lambda repos: repos.book_places.find(place.key)) is None)
        expect(await stored_profiles(fx_uow(), fx_project.owner_id) == [])
        await kind.run(uow, fx_project, add_both)
        expect(await fx_uow().read(lambda repos: repos.book_places.find(place.key)) == place)
        expect(await stored_profiles(fx_uow(), fx_project.owner_id) == [profile])
        assert_expectations()


class TestChangeBook:
    """Contract of ChangeBook.change_book() on the project it hands its work."""

    async def test_work_receives_the_stored_project(self, fx_uow: UnitOfWorkFactory, fx_project: Project) -> None:
        """Verify the work is handed the project as stored, beside the repos.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project the change belongs to.
        :type fx_project: Project
        """
        received = await fx_uow().change_book(fx_project.id, lambda _repos, project: project)
        assert received == fx_project

    async def test_missing_book_raises_not_found_and_runs_no_work(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a change of a project that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        missing = make_project(owner_id=await fx_new_owner())
        ran: list[bool] = []
        uow = fx_uow()
        with pytest.raises(NotFoundError):
            await uow.change_book(missing.id, lambda _repos, _project: ran.append(True))
        assert ran == []

    async def test_work_receives_the_project_as_the_previous_change_left_it(
        self, fx_uow: UnitOfWorkFactory, fx_project: Project
    ) -> None:
        """Verify a change reads the project once the change before it has committed, not as it was stored first.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project both changes belong to.
        :type fx_project: Project
        """
        changed = evolve(fx_project, image_policy=ImagePolicy.LOSSLESS)
        first, second = fx_uow(), fx_uow()
        await first.change_book(fx_project.id, lambda repos, _project: repos.projects.update(changed))
        received = await second.change_book(fx_project.id, lambda _repos, project: project)
        assert received == changed


class TestRead:
    """Contract of UnitOfWork.read() on what it returns and what it sees."""

    async def test_read_returns_what_the_work_returns(self, fx_uow: UnitOfWorkFactory, fx_project: Project) -> None:
        """Verify ``read`` hands back the value of its work.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project that is read.
        :type fx_project: Project
        """
        read = await fx_uow().read(lambda repos: repos.projects.get(fx_project.id))
        assert read == fx_project

    async def test_read_sees_every_write_of_a_committed_change_and_none_of_a_rolled_back_one(
        self, fx_uow: UnitOfWorkFactory, fx_project: Project
    ) -> None:
        """Verify a read sees the two rows of a change that committed, and nothing of a change that rolled back.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_project: Stored project the rows belong to.
        :type fx_project: Project
        """
        kept = make_recipe_profile(account_id=fx_project.owner_id)
        place = make_book_place(account_id=fx_project.owner_id, project_id=fx_project.id)
        lost = make_recipe_profile(account_id=fx_project.owner_id, minutes=1)
        uow = fx_uow()

        def add_kept(repos: Repositories) -> None:
            """Add a recipe profile and a place of the book.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            repos.recipe_profiles.add(kept)
            repos.book_places.add(place)

        def add_lost(repos: Repositories) -> None:
            """Add another recipe profile and raise, so the change rolls back.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :raises DomainError: Always.
            """
            repos.recipe_profiles.add(lost)
            raise DomainError

        def read_both(repos: Repositories) -> tuple[list[RecipeProfile], object]:
            """Read the recipe profiles of the account and the place, in one read.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The profiles of the account in every stage, and the place of the account in the book.
            :rtype: tuple[list[RecipeProfile], object]
            """
            profiles = list(repos.recipe_profiles.list_for_account(fx_project.owner_id))
            return profiles, repos.book_places.find(place.key)

        await uow.change(add_kept)
        with pytest.raises(DomainError):
            await uow.change(add_lost)
        assert await fx_uow().read(read_both) == ([kept], place)


class TestChangeWait:
    """Contract of what a change waits for: any other change of the same book, for as long as the limit.

    A book is held by a work that blocks the thread it runs in, and SQLite has one writer, so while it is held every
    other change waits, whatever book it changes.
    """

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_waits_until_the_book_is_free(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_hold_book: BookHolder,
        fx_project: Project,
        kind: ChangeKind,
    ) -> None:
        """Verify a change does not run while another change holds its book, and runs once the book is free.

        The test lets every task block, and checks that the work has not run, before it frees the book.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_hold_book: Function holding the lock of a book.
        :type fx_hold_book: BookHolder
        :param fx_project: Stored project the changes belong to.
        :type fx_project: Project
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        holder, waiter = fx_uow(), fx_uow()
        ran: list[str] = []
        ran_while_held: list[str] = []

        async def wait_and_run() -> None:
            """Ask for the change and note when its work runs."""
            await kind.run(waiter, fx_project, lambda _repos: ran.append(SECOND_IN))

        async with anyio.create_task_group() as tasks, fx_hold_book(holder, kind.lock_of(fx_project)):
            tasks.start_soon(wait_and_run)
            await anyio.wait_all_tasks_blocked()
            ran_while_held.extend(ran)
        expect(ran_while_held == [])
        expect(ran == [SECOND_IN])
        assert_expectations()

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_change_reads_what_the_changes_before_it_committed(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_hold_book: BookHolder,
        fx_project: Project,
        kind: ChangeKind,
    ) -> None:
        """Verify two changes that queue behind a held book run one after the other, the second reading the first.

        Each change is started only once every task is blocked, so the first asked before the second.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_hold_book: Function holding the lock of a book.
        :type fx_hold_book: BookHolder
        :param fx_project: Stored project the changes belong to.
        :type fx_project: Project
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        first_profile = make_recipe_profile(account_id=fx_project.owner_id)
        second_profile = make_recipe_profile(account_id=fx_project.owner_id, minutes=1)
        holder, first, second = fx_uow(), fx_uow(), fx_uow()
        seen: list[bool] = []

        def add_first(repos: Repositories) -> None:
            """Add the first recipe profile.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            repos.recipe_profiles.add(first_profile)

        def add_second(repos: Repositories) -> None:
            """Note whether the first recipe profile is stored, and add the second one.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            listed = repos.recipe_profiles.list_for_account(fx_project.owner_id)
            seen.append(first_profile.id in {profile.id for profile in listed})
            repos.recipe_profiles.add(second_profile)

        async with anyio.create_task_group() as tasks, fx_hold_book(holder, kind.lock_of(fx_project)):
            tasks.start_soon(kind.run, first, fx_project, add_first)
            await anyio.wait_all_tasks_blocked()
            tasks.start_soon(kind.run, second, fx_project, add_second)
            await anyio.wait_all_tasks_blocked()
        expect(seen == [True])
        expect(len(await stored_profiles(fx_uow(), fx_project.owner_id)) == 2)
        assert_expectations()

    async def test_two_changes_of_a_book_that_read_and_write_one_page_keep_both_writes(
        self, fx_uow: UnitOfWorkFactory, fx_hold_book: BookHolder, fx_project: Project
    ) -> None:
        """Verify two changes that each read a page and write another field of it both keep their writes.

        Both are started while the book is held, so each would write the page as it read it if the second were let in
        before the first had committed.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_hold_book: Function holding the lock of a book.
        :type fx_hold_book: BookHolder
        :param fx_project: Stored project both changes belong to.
        :type fx_project: Project
        """
        page = make_page(project_id=fx_project.id)
        holder, first, second = fx_uow(), fx_uow(), fx_uow()
        await first.change_book(fx_project.id, lambda repos, _project: repos.pages.add(page))

        def label_page(repos: Repositories, _project: Project) -> None:
            """Read the page and write its label.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.pages.update(evolve(repos.pages.get(page.id), label=FIRST_LABEL))

        def note_page(repos: Repositories, _project: Project) -> None:
            """Read the page and write its notes.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.pages.update(evolve(repos.pages.get(page.id), notes=STAMP))

        async with anyio.create_task_group() as tasks, fx_hold_book(holder, fx_project.id):
            tasks.start_soon(first.change_book, fx_project.id, label_page)
            await anyio.wait_all_tasks_blocked()
            tasks.start_soon(second.change_book, fx_project.id, note_page)
            await anyio.wait_all_tasks_blocked()
        stored = await fx_uow().read(lambda repos: repos.pages.get(page.id))
        assert (stored.label, stored.notes) == (FIRST_LABEL, STAMP)

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_changes_waiting_for_one_holder_run_in_the_order_they_asked(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_hold_book: BookHolder,
        fx_project: Project,
        kind: ChangeKind,
    ) -> None:
        """Verify changes that queue behind a held book run one by one in the order they asked, first come first in.

        Each waiter is started only once every task is blocked, so it asks after the one before it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_hold_book: Function holding the lock of a book.
        :type fx_hold_book: BookHolder
        :param fx_project: Stored project every change belongs to.
        :type fx_project: Project
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
            await kind.run(waiters[number], fx_project, lambda _repos: ran.append(number))

        async with anyio.create_task_group() as tasks, fx_hold_book(holder, kind.lock_of(fx_project)):
            for number in range(WAITERS):
                tasks.start_soon(wait_and_run, number)
                await anyio.wait_all_tasks_blocked()
        assert ran == list(range(WAITERS))

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_wait_longer_than_the_limit_raises_book_busy_and_runs_no_work(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_hold_book: BookHolder,
        fx_project: Project,
        kind: ChangeKind,
    ) -> None:
        """Verify a change gives up with ``BookBusyError`` after the limit of its unit of work, and runs no work.

        The unit of work has the limit of a request, so the wait ends in a fraction of a second whatever the holder
        does. Once the holder has let go, the same unit of work runs the change at once.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_hold_book: Function holding the lock of a book.
        :type fx_hold_book: BookHolder
        :param fx_project: Stored project the changes belong to.
        :type fx_project: Project
        :param kind: The kind of change under test.
        :type kind: ChangeKind
        """
        holder, impatient = fx_uow(), fx_uow(wait_seconds=REQUEST_WAIT_SECONDS)
        ran: list[str] = []
        async with fx_hold_book(holder, kind.lock_of(fx_project)):
            started = perf_counter()
            with pytest.raises(BookBusyError):
                await kind.run(impatient, fx_project, lambda _repos: ran.append(SECOND_IN))
            gave_up_after = perf_counter() - started
        ran_while_held = list(ran)
        await kind.run(impatient, fx_project, lambda _repos: ran.append(SECOND_IN))
        expect(gave_up_after < REFUSAL_BOUND_SECONDS)
        expect(ran_while_held == [])
        expect(ran == [SECOND_IN])
        assert_expectations()

    @pytest.mark.parametrize(KIND_ARG, KIND_PARAMS, ids=KIND_IDS)
    async def test_wait_longer_than_the_limit_of_a_request_enters_under_the_limit_of_a_job(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_hold_book: BookHolder,
        fx_project: Project,
        kind: ChangeKind,
    ) -> None:
        """Verify a unit of work with the limit of a job waits for the holder past the limit of a request.

        A unit of work with the limit of a request gives up while the holder still holds the book, which proves that
        more than that limit has passed, and only then is the holder let go. The unit of work of the job asked before
        the request one, so it has waited at least as long, and runs after the holder instead of raising
        ``BookBusyError``.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_hold_book: Function holding the lock of a book.
        :type fx_hold_book: BookHolder
        :param fx_project: Stored project every change belongs to.
        :type fx_project: Project
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
            await kind.run(job, fx_project, lambda _repos: order.append(SECOND_IN))

        async with anyio.create_task_group() as tasks, fx_hold_book(holder, kind.lock_of(fx_project)):
            tasks.start_soon(wait_as_a_job)
            await anyio.wait_all_tasks_blocked()
            with pytest.raises(BookBusyError):
                await kind.run(request, fx_project, lambda _repos: order.append(REQUEST_GAVE_UP))
            order.append(HOLDER_RELEASED)
        assert order == [HOLDER_RELEASED, SECOND_IN]


class TestWriteGuard:
    """Contract of NoChangeOpenError: no repository writes in the work of a read."""

    async def test_every_repository_has_its_rows(self, fx_rows: dict[str, Row]) -> None:
        """Verify the rows of the guard test cover every repository the port declares.

        :param fx_rows: Rows by the name of the repository that holds them.
        :type fx_rows: dict[str, Row]
        """
        assert set(fx_rows) == set(Repositories.__annotations__)

    @pytest.mark.parametrize(('repository', 'method'), WRITE_PARAMS)
    async def test_write_in_a_read_raises(
        self, fx_uow: UnitOfWorkFactory, fx_rows: dict[str, Row], repository: str, method: str
    ) -> None:
        """Verify a repository refuses a write in the work of ``read``.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_rows: Rows by the name of the repository that holds them.
        :type fx_rows: dict[str, Row]
        :param repository: Name of the repository of the port.
        :type repository: str
        :param method: Name of the write method of the repository.
        :type method: str
        """
        uow = fx_uow()
        with pytest.raises(NoChangeOpenError):
            await uow.read(lambda repos: WRITES[method](getattr(repos, repository), fx_rows[repository]))
