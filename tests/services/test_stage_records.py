"""Tests for the stage records a change of a recipe marks stale, over the in-memory adapter."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import Stage, StageState
from bookreviver.services.stage_records import StageRecords
from tests.helpers.builders import EPOCH, make_page, make_page_stage, make_project, make_recipe, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Project
    from bookreviver.ports.persistence import Repositories
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

# The time the clock shows when the recipe changes, apart from the epoch the records are seeded at
CHANGED_AT = EPOCH + timedelta(hours=1)


class TestMarkRecipeStale:
    """Tests for ``StageRecords.mark_recipe_stale``."""

    async def test_the_fresh_records_of_the_recipe_end_stale_and_the_others_stay_as_they_were(
        self, fx_database: InMemoryDatabase, fx_clock: FixedClock, fx_events: RecordingEventBus
    ) -> None:
        """Verify the fresh records of the recipe are stamped stale together, and a failed one or one of no recipe stays.

        :param fx_database: Empty in-memory database.
        :type fx_database: InMemoryDatabase
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        :param fx_events: Recording event bus the records are announced through.
        :type fx_events: RecordingEventBus
        """
        project = make_project(owner_id=new_account_id())
        first, second, third = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1', 'a2'))
        await commit_project(fx_database, project, first, second, third)
        recipe = make_recipe(project_id=project.id)
        fresh_first = make_page_stage(page_id=first.id, recipe_id=recipe.id)
        fresh_second = make_page_stage(page_id=second.id, recipe_id=recipe.id)
        failed = make_page_stage(page_id=third.id, recipe_id=recipe.id, state=StageState.FAILED)
        without_recipe = make_page_stage(page_id=first.id, stage=Stage.CLEANUP)
        uow = InMemoryUnitOfWork(fx_database)
        records = StageRecords(publisher=fx_events, clock=fx_clock)

        def seed(repos: Repositories, _project: Project) -> None:
            """Store the recipe and the four records.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.recipes.add(recipe)
            repos.page_stages.save_many([fresh_first, fresh_second, failed, without_recipe])

        await uow.change_book(project.id, seed)
        fx_clock.moment = CHANGED_AT

        marked = await uow.change_book(project.id, lambda repos, _project: records.mark_recipe_stale(repos, recipe.id))

        stored = await uow.read(lambda repos: repos.page_stages.list_for_pages([first.id, second.id, third.id]))
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
