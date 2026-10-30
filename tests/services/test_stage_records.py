"""Tests for the stage records a change of a recipe marks stale."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.services.stage_records import StageRecords
from tests.helpers.builders import make_page, make_page_stage, make_recipe, make_record, new_account_id
from tests.helpers.seeding import commit_record

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.recipes import Recipe
    from bookreviver.domain.stages import PageStage
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases
    from tests.helpers.page_stage_writes import PageStageWrites

pytestmark = pytest.mark.anyio

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

    async def test_the_records_of_the_recipe_are_written_by_one_save_many_and_never_by_save(
        self,
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_page_stage_writes: PageStageWrites,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify three fresh records are written by one ``save_many`` holding all three, and ``save`` is not called.

        The end state of the records is the same when each one is written on its own, so only a record of the calls
        tells a batch of one statement from a statement for each record.

        :param fx_database: Database of the test, holding no book yet.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_page_stage_writes: Record of the writes of the page stage repository, not yet started.
        :type fx_page_stage_writes: PageStageWrites
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        pages = [make_page(order_key=key) for key in PAGE_ORDER_KEYS]
        book_id = await commit_record(
            fx_database, fx_catalogue(), make_record(), *pages, owner=new_account_id(), databases=fx_databases()
        )
        recipe = make_recipe()
        fresh = [make_page_stage(page_id=page.id, recipe_id=recipe.id) for page in pages]
        records = StageRecords()
        await store_records(book_id, recipe, fresh, databases=fx_databases())
        fx_page_stage_writes.start()

        await (
            fx_databases().open(book_id).change(lambda repos, _book_record: records.mark_recipe_stale(repos, recipe.id))
        )

        [batch] = fx_page_stage_writes.batches
        assert ({record.key for record in batch}, fx_page_stage_writes.saves) == ({record.key for record in fresh}, [])
