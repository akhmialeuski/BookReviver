"""Tests for the stage records a change of a recipe marks stale."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import Stage, StageState
from bookreviver.services.stage_records import StageRecords
from tests.helpers.builders import EPOCH, make_page, make_page_stage, make_recipe, make_record, new_account_id
from tests.helpers.seeding import commit_record

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import BookRecord, PageStage, Recipe
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.clock import FixedClock
    from tests.helpers.database import BookDatabasesFactory, LocatedBookDatabases, UnitOfWorkFactory
    from tests.helpers.page_stage_writes import PageStageWrites

pytestmark = pytest.mark.anyio

# The time the clock shows when the recipe changes, apart from the epoch the records are seeded at
CHANGED_AT = EPOCH + timedelta(hours=1)
# The places of the three pages the tests seed
PAGE_ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')


async def store_records(
    book_id: BookId, recipe: Recipe, records: Sequence[PageStage], *, databases: LocatedBookDatabases
) -> None:
    """Store a recipe and the stage records of the pages of a book that exists already.

    :param book_id: Identifier of the book owning the pages the records belong to.
    :type book_id: BookId
    :param recipe: Recipe of the book, which the records may name.
    :type recipe: Recipe
    :param records: Records to store in one batch.
    :type records: Sequence[PageStage]
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    """

    def seed(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Store the recipe and the records.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        repos.recipes.add(recipe)
        repos.page_stages.save_many(records)

    await databases.open(book_id).change(seed)


class TestMarkRecipeStale:
    """Tests for ``StageRecords.mark_recipe_stale``."""

    async def test_the_fresh_records_of_the_recipe_end_stale_and_the_others_stay_as_they_were(
        self,
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_clock: FixedClock,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the fresh records of the recipe are stamped stale together, and a failed one or one of no recipe stays.

        :param fx_database: Database of the test, holding no book yet.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first, second, third = (make_page(order_key=key) for key in PAGE_ORDER_KEYS)
        book_id = await commit_record(
            fx_database, fx_uow(), make_record(), first, second, third, owner=new_account_id(), databases=fx_databases()
        )
        recipe = make_recipe()
        fresh_first = make_page_stage(page_id=first.id, recipe_id=recipe.id)
        fresh_second = make_page_stage(page_id=second.id, recipe_id=recipe.id)
        failed = make_page_stage(page_id=third.id, recipe_id=recipe.id, state=StageState.FAILED)
        without_recipe = make_page_stage(page_id=first.id, stage=Stage.CLEANUP)
        records = StageRecords(clock=fx_clock)
        await store_records(
            book_id, recipe, [fresh_first, fresh_second, failed, without_recipe], databases=fx_databases()
        )
        fx_clock.moment = CHANGED_AT

        await (
            fx_databases().open(book_id).change(lambda repos, _book_record: records.mark_recipe_stale(repos, recipe.id))
        )

        stored = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_stages.list_for_pages([first.id, second.id, third.id]))
        )
        stale = [
            evolve(record, state=StageState.STALE, updated_at=CHANGED_AT) for record in (fresh_first, fresh_second)
        ]
        assert {record.key: record for record in stored} == {
            record.key: record for record in (*stale, failed, without_recipe)
        }

    async def test_the_records_of_the_recipe_are_written_by_one_save_many_and_never_by_save(
        self,
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_clock: FixedClock,
        fx_page_stage_writes: PageStageWrites,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify three fresh records are written by one ``save_many`` holding all three, and ``save`` is not called.

        The end state of the records is the same when each one is written on its own, so only a record of the calls
        tells a batch of one statement from a statement for each record.

        :param fx_database: Database of the test, holding no book yet.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        :param fx_page_stage_writes: Record of the writes of the page stage repository, not yet started.
        :type fx_page_stage_writes: PageStageWrites
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        pages = [make_page(order_key=key) for key in PAGE_ORDER_KEYS]
        book_id = await commit_record(
            fx_database, fx_uow(), make_record(), *pages, owner=new_account_id(), databases=fx_databases()
        )
        recipe = make_recipe()
        fresh = [make_page_stage(page_id=page.id, recipe_id=recipe.id) for page in pages]
        records = StageRecords(clock=fx_clock)
        await store_records(book_id, recipe, fresh, databases=fx_databases())
        fx_clock.moment = CHANGED_AT
        fx_page_stage_writes.start()

        await (
            fx_databases().open(book_id).change(lambda repos, _book_record: records.mark_recipe_stale(repos, recipe.id))
        )

        [batch] = fx_page_stage_writes.batches
        assert ({record.key for record in batch}, fx_page_stage_writes.saves) == ({record.key for record in fresh}, [])
