"""Contract of the blocks of the unit of work, run against every adapter registered in the conftest.

``change_book`` and ``change`` commit when they end normally, roll back when an exception leaves them, wait for any
other change of the same book, refuse a write outside a block and refuse a block inside a block. Concurrency is real:
two tasks of an ``anyio`` task group, ordered by events and ``anyio.wait_all_tasks_blocked`` and never by a sleep.
"""

from datetime import timedelta
from itertools import product
from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobState, Stage, ValueScope
from bookreviver.domain.errors import BookBusyError, DomainError, NotFoundError
from bookreviver.domain.values import PageStepKey
from bookreviver.ports.persistence import NestedChangeError, NoChangeOpenError, UnitOfWork
from tests.helpers.builders import (
    make_book_place,
    make_job,
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
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# The wait limit of the suite, short enough for a test of a refused wait and long enough for a wait that ends in time
WAIT_SECONDS: float = 0.5
ONE_MINUTE: timedelta = timedelta(minutes=1)
LEFT_THE_BLOCK: str = 'left the block'
RENAMED: str = 'Renamed'
STAMP: str = 'Stamp'
# The steps a block of the order tests takes, which the tests compare against the order they must happen in
FIRST_IN: str = 'first in'
FIRST_OUT: str = 'first out'
SECOND_IN: str = 'second in'
OPENER_ARG: str = 'opener'
OPENER_IDS: list[str] = ['change_book', 'change']
ERROR_ARG: str = 'error_type'
# A programming mistake and a rule of the domain, which a block treats alike
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
    'update_if_state',
    'save',
)

type Opener = Callable[[UnitOfWork, Project], AbstractAsyncContextManager[object]]

OPENERS: dict[str, Opener] = {
    'change_book': lambda uow, project: uow.change_book(project.id),
    'change': lambda uow, _project: uow.change(),
}
OPENER_PARAMS: list[Opener] = list(OPENERS.values())
# Every pair of an open block and a block opened inside it
NESTINGS: list[Any] = [
    pytest.param(OPENERS[outer], OPENERS[inner], id=f'{inner}-inside-{outer}')
    for outer, inner in product(OPENERS, repeat=2)
]


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
WRITES: dict[str, Callable[[Any, Row], Awaitable[object]]] = {
    'add': lambda repository, row: repository.add(row.fresh),
    'add_many': lambda repository, row: repository.add_many([row.fresh]),
    'update': lambda repository, row: repository.update(row.changed),
    'update_many': lambda repository, row: repository.update_many([row.changed]),
    'delete': lambda repository, row: repository.delete(row.key),
    'delete_many': lambda repository, row: repository.delete_many([row.key]),
    'delete_for_step': lambda repository, row: repository.delete_for_step(
        PageStepKey(page_id=row.stored.page_id, stage=row.stored.stage, step_id=row.stored.step_id)
    ),
    'update_if_state': lambda repository, row: repository.update_if_state(row.changed, expected=JobState.active()),
    'save': lambda repository, row: repository.save(row.changed),
}
# Each repository of the unit of work with each write method it has, which the port's own declaration lists
WRITE_PARAMS: list[Any] = [
    pytest.param(repository, method, id=f'{repository}.{method}')
    for repository, repository_type in UnitOfWork.__annotations__.items()
    for method in WRITE_METHODS
    if hasattr(repository_type, method)
]


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Shorten the wait limit of a change, so a refused wait takes a fraction of a second.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: The same settings with the wait limit of the suite.
    :rtype: Settings
    """
    return fx_settings.model_copy(update={'change_wait_seconds': WAIT_SECONDS})


@pytest.fixture
async def fx_project(fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> Project:
    """Store a project that the blocks of the tests change.

    :param fx_uow_factory: Function opening a new unit of work of the backend under test.
    :type fx_uow_factory: UnitOfWorkFactory
    :param fx_new_owner: Function creating an account the backend accepts as an owner.
    :type fx_new_owner: OwnerFactory
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=await fx_new_owner())
    await store_project(await fx_uow_factory(), project)
    return project


@pytest.fixture
async def fx_book_rows(fx_uow_factory: UnitOfWorkFactory, fx_project: Project) -> dict[str, Row]:
    """Store one row in every repository that holds the content of a book, and build a changed copy and a new row.

    :param fx_uow_factory: Function opening a new unit of work of the backend under test.
    :type fx_uow_factory: UnitOfWorkFactory
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
    uow = await fx_uow_factory()
    async with uow.change_book(project.id):
        await uow.sources.add(source)
        await uow.scans.add(scan)
        await uow.pages.add(page)
        await uow.pagination_sections.add(section)
        await uow.page_versions.add(version)
        await uow.page_stages.add(stage)
        await uow.page_step_states.add(state)
        await uow.step_values.add(values)
        await uow.page_step_changes.add(step_change)
        await uow.result_mark_changes.add(mark_change)
        await uow.recipes.add(recipe)
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
async def fx_rows(
    fx_uow_factory: UnitOfWorkFactory, fx_project: Project, fx_book_rows: dict[str, Row]
) -> dict[str, Row]:
    """Store one row in every other repository too, and build a changed copy and a new row beside each.

    :param fx_uow_factory: Function opening a new unit of work of the backend under test.
    :type fx_uow_factory: UnitOfWorkFactory
    :param fx_project: Stored project, which owns the jobs and the places.
    :type fx_project: Project
    :param fx_book_rows: Rows of the repositories that hold the content of the book.
    :type fx_book_rows: dict[str, Row]
    :returns: The rows by the name of the repository that holds them, for every repository of the unit of work.
    :rtype: dict[str, Row]
    """
    project, owner_id = fx_project, fx_project.owner_id
    profile = make_recipe_profile(account_id=owner_id)
    job = make_job(project_id=project.id, state=JobState.SUCCEEDED)
    place = make_book_place(account_id=owner_id, project_id=project.id)
    uow = await fx_uow_factory()
    async with uow.change():
        await uow.recipe_profiles.add(profile)
        await uow.jobs.add(job)
        await uow.book_places.add(place)
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
        'jobs': Row(job, evolve(job, created_at=job.created_at + ONE_MINUTE), make_job(project_id=project.id), job.id),
        'book_places': Row(
            place,
            evolve(place, updated_at=place.updated_at + ONE_MINUTE),
            make_book_place(account_id=owner_id, project_id=project.id, stage=Stage.CLEANUP),
            place.key,
        ),
    }


class TestBlockEnd:
    """Contract of how a block ends: it commits on a normal exit and rolls back on an exception."""

    @pytest.mark.parametrize(OPENER_ARG, OPENER_PARAMS, ids=OPENER_IDS)
    async def test_block_commits_on_a_normal_exit(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project, opener: Opener
    ) -> None:
        """Verify what a block wrote is visible to another unit of work once the block has exited.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project the block changes.
        :type fx_project: Project
        :param opener: Opens the kind of block under test.
        :type opener: Opener
        """
        job = make_job(project_id=fx_project.id)
        uow = await fx_uow_factory()
        async with opener(uow, fx_project):
            await uow.jobs.add(job)
        assert await (await fx_uow_factory()).jobs.get(job.id) == job

    @pytest.mark.parametrize(OPENER_ARG, OPENER_PARAMS, ids=OPENER_IDS)
    @pytest.mark.parametrize(ERROR_ARG, ERROR_TYPES, ids=[error.__name__ for error in ERROR_TYPES])
    async def test_block_rolls_back_and_reraises_an_exception(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project, opener: Opener, error_type: type[Exception]
    ) -> None:
        """Verify an exception leaving a block discards what the block wrote, reaches the caller, and frees the book.

        The same unit of work then writes the same job in a new block, which proves the book is free again and that
        nothing of the first block was kept.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project the block changes.
        :type fx_project: Project
        :param opener: Opens the kind of block under test.
        :type opener: Opener
        :param error_type: Class of the exception that leaves the block.
        :type error_type: type[Exception]
        """
        job = make_job(project_id=fx_project.id)
        uow = await fx_uow_factory()

        async def leave_by_exception() -> None:
            """Write the job in a block, and leave the block by an exception."""
            async with opener(uow, fx_project):
                await uow.jobs.add(job)
                raise error_type(LEFT_THE_BLOCK)

        with pytest.raises(error_type, match=LEFT_THE_BLOCK):
            await leave_by_exception()
        expect(await (await fx_uow_factory()).jobs.list_for_project(fx_project.id, set(JobState)) == [])
        async with opener(uow, fx_project):
            await uow.jobs.add(job)
        expect(await (await fx_uow_factory()).jobs.get(job.id) == job)
        assert_expectations()

    @pytest.mark.parametrize(OPENER_ARG, OPENER_PARAMS, ids=OPENER_IDS)
    async def test_repository_taken_before_a_block_reads_and_writes_inside_it(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project, opener: Opener
    ) -> None:
        """Verify a repository of a unit of work stays the one its blocks read and write through.

        The repository is taken before another unit of work commits a job, so a repository bound to the state of an
        earlier transaction would miss that job inside the block and write the new one where no commit reaches it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project the block changes.
        :type fx_project: Project
        :param opener: Opens the kind of block under test.
        :type opener: Opener
        """
        committed, written = make_job(project_id=fx_project.id), make_job(project_id=fx_project.id, minutes=1)
        uow = await fx_uow_factory()
        jobs = uow.jobs
        other = await fx_uow_factory()
        async with other.change():
            await other.jobs.add(committed)
        async with opener(uow, fx_project):
            expect(await jobs.get(committed.id) == committed)
            await jobs.add(evolve(written, state=JobState.SUCCEEDED))
        expect(await (await fx_uow_factory()).jobs.get(written.id) == evolve(written, state=JobState.SUCCEEDED))
        assert_expectations()


class TestChangeBook:
    """Contract of ChangeBook.change_book() on the project it opens."""

    async def test_block_yields_the_project(self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project) -> None:
        """Verify the block yields the stored project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project the block changes.
        :type fx_project: Project
        """
        async with (await fx_uow_factory()).change_book(fx_project.id) as project:
            assert project == fx_project

    async def test_missing_book_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a block of a project that is not stored does not open.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        missing = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError):
            async with uow.change_book(missing.id):
                pass


class TestBlockWait:
    """Contract of what a block waits for: any other block of the same kind and book, for as long as the limit."""

    async def test_second_change_of_a_book_waits_for_the_first_and_reads_its_write(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project
    ) -> None:
        """Verify a second ``change_book`` of one book does not enter before the first exits, then reads its write.

        The first block holds the book until the test releases it. The test lets every task block, and checks that the
        second block has not entered, before it releases the first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project both blocks change.
        :type fx_project: Project
        """
        page = make_page(project_id=fx_project.id)
        first, second = await fx_uow_factory(), await fx_uow_factory()
        async with first.change_book(fx_project.id):
            await first.pages.add(page)
        order: list[str] = []
        while_first_holds: list[str] = []
        labels: list[str] = []
        inside, release = anyio.Event(), anyio.Event()

        async def first_change() -> None:
            """Hold the book until released, and write a label of the page."""
            async with first.change_book(fx_project.id):
                order.append(FIRST_IN)
                inside.set()
                await release.wait()
                await first.pages.update(evolve(await first.pages.get(page.id), label='1'))
                order.append(FIRST_OUT)

        async def second_change() -> None:
            """Enter the book after the first block has, and read the label of the page."""
            await inside.wait()
            async with second.change_book(fx_project.id):
                order.append(SECOND_IN)
                labels.append((await second.pages.get(page.id)).label)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first_change)
            tasks.start_soon(second_change)
            await inside.wait()
            await anyio.wait_all_tasks_blocked()
            while_first_holds.extend(order)
            release.set()
        expect(while_first_holds == [FIRST_IN])
        expect(order == [FIRST_IN, FIRST_OUT, SECOND_IN])
        expect(labels == ['1'])
        assert_expectations()

    async def test_two_blocks_reading_and_writing_the_same_page_keep_both_writes(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project
    ) -> None:
        """Verify two blocks that each read a page and write another field of it both keep their writes.

        The second block starts while the first holds the book after reading the page, so it reads the page only once
        the first has committed, and does not write over the label with the page as it was.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project both blocks change.
        :type fx_project: Project
        """
        page = make_page(project_id=fx_project.id)
        first, second = await fx_uow_factory(), await fx_uow_factory()
        async with first.change_book(fx_project.id):
            await first.pages.add(page)
        read, release = anyio.Event(), anyio.Event()

        async def first_change() -> None:
            """Read the page, hold the book until released, and write its label."""
            async with first.change_book(fx_project.id):
                stored = await first.pages.get(page.id)
                read.set()
                await release.wait()
                await first.pages.update(evolve(stored, label='1'))

        async def second_change() -> None:
            """Enter the book after the first block has read the page, and write its notes."""
            await read.wait()
            async with second.change_book(fx_project.id):
                await second.pages.update(evolve(await second.pages.get(page.id), notes=STAMP))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first_change)
            tasks.start_soon(second_change)
            await read.wait()
            await anyio.wait_all_tasks_blocked()
            release.set()
        stored = await (await fx_uow_factory()).pages.get(page.id)
        assert (stored.label, stored.notes) == ('1', STAMP)

    async def test_two_changes_outside_a_book_run_one_after_the_other(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project
    ) -> None:
        """Verify a second ``change`` does not enter before the first exits, and then sees what the first stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project that owns the jobs the blocks add.
        :type fx_project: Project
        """
        # Finished jobs, because a project may have only one active job of a kind
        first_job = make_job(project_id=fx_project.id, state=JobState.SUCCEEDED)
        second_job = make_job(project_id=fx_project.id, state=JobState.SUCCEEDED, minutes=1)
        first, second = await fx_uow_factory(), await fx_uow_factory()
        order: list[str] = []
        while_first_holds: list[str] = []
        seen: list[bool] = []
        inside, release = anyio.Event(), anyio.Event()

        async def first_change() -> None:
            """Hold the lock of the changes outside books until released, and add a job."""
            async with first.change():
                order.append(FIRST_IN)
                inside.set()
                await release.wait()
                await first.jobs.add(first_job)
                order.append(FIRST_OUT)

        async def second_change() -> None:
            """Enter after the first block has, check that its job is stored, and add another job."""
            await inside.wait()
            async with second.change():
                order.append(SECOND_IN)
                seen.append(
                    first_job.id in {job.id for job in await second.jobs.list_for_project(fx_project.id, set(JobState))}
                )
                await second.jobs.add(second_job)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first_change)
            tasks.start_soon(second_change)
            await inside.wait()
            await anyio.wait_all_tasks_blocked()
            while_first_holds.extend(order)
            release.set()
        expect(while_first_holds == [FIRST_IN])
        expect(order == [FIRST_IN, FIRST_OUT, SECOND_IN])
        expect(seen == [True])
        expect(len(await (await fx_uow_factory()).jobs.list_for_project(fx_project.id, set(JobState))) == 2)
        assert_expectations()

    @pytest.mark.parametrize(OPENER_ARG, OPENER_PARAMS, ids=OPENER_IDS)
    async def test_wait_longer_than_the_limit_raises_book_busy(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project, opener: Opener
    ) -> None:
        """Verify a block that cannot enter within the wait limit of the settings raises ``BookBusyError``.

        Once the first block has exited, the second unit of work opens the same block at once.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project both blocks change.
        :type fx_project: Project
        :param opener: Opens the kind of block under test.
        :type opener: Opener
        """
        first, second = await fx_uow_factory(), await fx_uow_factory()
        async with opener(first, fx_project):
            with pytest.raises(BookBusyError):
                async with opener(second, fx_project):
                    pass
        async with opener(second, fx_project):
            pass


class TestNesting:
    """Contract of NestedChangeError: a unit of work never opens a block inside one of its own."""

    @pytest.mark.parametrize(('outer', 'inner'), NESTINGS)
    async def test_block_inside_a_block_raises(
        self, fx_uow_factory: UnitOfWorkFactory, fx_project: Project, outer: Opener, inner: Opener
    ) -> None:
        """Verify the second block of one unit of work is refused at once, and the first block ends as usual.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_project: Stored project both blocks would change.
        :type fx_project: Project
        :param outer: Opens the block that is open already.
        :type outer: Opener
        :param inner: Opens the block that is refused.
        :type inner: Opener
        """
        job = make_job(project_id=fx_project.id)
        uow = await fx_uow_factory()
        async with outer(uow, fx_project):
            with pytest.raises(NestedChangeError):
                async with inner(uow, fx_project):
                    pass
            await uow.jobs.add(job)
        assert await (await fx_uow_factory()).jobs.get(job.id) == job


class TestWriteGuard:
    """Contract of NoChangeOpenError: no repository writes while no block is open."""

    async def test_every_repository_has_its_rows(self, fx_rows: dict[str, Row]) -> None:
        """Verify the rows of the guard test cover every repository the port declares.

        :param fx_rows: Rows by the name of the repository that holds them.
        :type fx_rows: dict[str, Row]
        """
        assert set(fx_rows) == set(UnitOfWork.__annotations__)

    @pytest.mark.parametrize(('repository', 'method'), WRITE_PARAMS)
    async def test_write_outside_a_block_raises(
        self, fx_uow_factory: UnitOfWorkFactory, fx_rows: dict[str, Row], repository: str, method: str
    ) -> None:
        """Verify a repository refuses a write while no block of its unit of work is open.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_rows: Rows by the name of the repository that holds them.
        :type fx_rows: dict[str, Row]
        :param repository: Name of the repository of the unit of work.
        :type repository: str
        :param method: Name of the write method of the repository.
        :type method: str
        """
        uow = await fx_uow_factory()
        with pytest.raises(NoChangeOpenError):
            await WRITES[method](getattr(uow, repository), fx_rows[repository])
