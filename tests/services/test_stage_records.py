"""Tests for the stage records a change of a recipe marks stale."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import Stage, StageState
from bookreviver.services.stage_records import StageRecords
from tests.helpers.builders import EPOCH, make_page, make_page_stage, make_project, make_recipe, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import PageStage, Project, Recipe
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from tests.helpers.clock import FixedClock
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import RecordingEventBus
    from tests.helpers.page_stage_writes import PageStageWrites

pytestmark = pytest.mark.anyio

# The time the clock shows when the recipe changes, apart from the epoch the records are seeded at
CHANGED_AT = EPOCH + timedelta(hours=1)
# The places of the three pages the tests seed
PAGE_ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')


async def store_records(uow: UnitOfWork, project: Project, recipe: Recipe, records: Sequence[PageStage]) -> None:
    """Store a recipe and the stage records of the pages of a project that exists already.

    :param uow: Unit of work that commits the recipe and the records.
    :type uow: UnitOfWork
    :param project: Project owning the pages the records belong to.
    :type project: Project
    :param recipe: Recipe of the project, which the records may name.
    :type recipe: Recipe
    :param records: Records to store in one batch.
    :type records: Sequence[PageStage]
    """

    def seed(repos: BookRepositories, _project: Project) -> None:
        """Store the recipe and the records.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.recipes.add(recipe)
        repos.page_stages.save_many(records)

    await uow.change_book(project.id, seed)


class TestMarkRecipeStale:
    """Tests for ``StageRecords.mark_recipe_stale``."""

    async def test_the_fresh_records_of_the_recipe_end_stale_and_the_others_stay_as_they_were(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_clock: FixedClock, fx_events: RecordingEventBus
    ) -> None:
        """Verify the fresh records of the recipe are stamped stale together, and a failed one or one of no recipe stays.

        :param fx_database: Database of the test, holding no project yet.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        :param fx_events: Recording event bus the records are announced through.
        :type fx_events: RecordingEventBus
        """
        project = make_project()
        first, second, third = (make_page(project_id=project.id, order_key=key) for key in PAGE_ORDER_KEYS)
        await commit_project(fx_database, fx_uow(), project, first, second, third, owner=new_account_id())
        recipe = make_recipe(project_id=project.id)
        fresh_first = make_page_stage(page_id=first.id, recipe_id=recipe.id)
        fresh_second = make_page_stage(page_id=second.id, recipe_id=recipe.id)
        failed = make_page_stage(page_id=third.id, recipe_id=recipe.id, state=StageState.FAILED)
        without_recipe = make_page_stage(page_id=first.id, stage=Stage.CLEANUP)
        uow = fx_uow()
        records = StageRecords(publisher=fx_events, clock=fx_clock)
        await store_records(uow, project, recipe, [fresh_first, fresh_second, failed, without_recipe])
        fx_clock.moment = CHANGED_AT

        marked = await uow.change_book(project.id, lambda repos, _project: records.mark_recipe_stale(repos, recipe.id))

        stored = await uow.read_book(
            project.id, lambda repos: repos.page_stages.list_for_pages([first.id, second.id, third.id])
        )
        stale = [
            evolve(record, state=StageState.STALE, updated_at=CHANGED_AT) for record in (fresh_first, fresh_second)
        ]
        assert (
            {record.key: record for record in marked},
            {record.key: record for record in stored},
        ) == (
            {record.key: record for record in stale},
            {record.key: record for record in (*stale, failed, without_recipe)},
        )

    async def test_the_records_of_the_recipe_are_written_by_one_save_many_and_never_by_save(
        self,
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_clock: FixedClock,
        fx_events: RecordingEventBus,
        fx_page_stage_writes: PageStageWrites,
    ) -> None:
        """Verify three fresh records are written by one ``save_many`` holding all three, and ``save`` is not called.

        The end state of the records is the same when each one is written on its own, so only a record of the calls
        tells a batch of one statement from a statement for each record.

        :param fx_database: Database of the test, holding no project yet.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        :param fx_events: Recording event bus the records are announced through.
        :type fx_events: RecordingEventBus
        :param fx_page_stage_writes: Record of the writes of the page stage repository, not yet started.
        :type fx_page_stage_writes: PageStageWrites
        """
        project = make_project()
        pages = [make_page(project_id=project.id, order_key=key) for key in PAGE_ORDER_KEYS]
        await commit_project(fx_database, fx_uow(), project, *pages, owner=new_account_id())
        recipe = make_recipe(project_id=project.id)
        fresh = [make_page_stage(page_id=page.id, recipe_id=recipe.id) for page in pages]
        uow = fx_uow()
        records = StageRecords(publisher=fx_events, clock=fx_clock)
        await store_records(uow, project, recipe, fresh)
        fx_clock.moment = CHANGED_AT
        fx_page_stage_writes.start()

        marked = await uow.change_book(project.id, lambda repos, _project: records.mark_recipe_stale(repos, recipe.id))

        assert (len(marked), fx_page_stage_writes.batches, fx_page_stage_writes.saves) == (3, [marked], [])
