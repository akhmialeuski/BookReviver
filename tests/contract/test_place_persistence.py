"""Contract of the persistence port of the place the reader left a book at.

Every test runs against the SQL adapter, so it keeps the promises of the port: one place for each book, a
replacement on every save that is whole, and the removal of the place with the book.
"""

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve

from bookreviver.domain.ids import PageId
from bookreviver.domain.places import CanvasPosition, PlaceMode
from bookreviver.domain.stages import Stage
from tests.harness.builders.domain import make_book_place, make_record
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.ids import BookId
    from tests.contract.conftest import OwnerFactory
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases

pytestmark = pytest.mark.anyio

# How much later one write of a place is stamped than another
LATER: timedelta = timedelta(minutes=5)


async def _store_book(
    catalogue_factory: CatalogueDatabaseFactory, new_owner: OwnerFactory, *, databases: LocatedBookDatabases
) -> BookId:
    """Store a book of a new account.

    :param catalogue_factory: Function opening a new catalogue database over the catalogue and the books of the test.
    :type catalogue_factory: CatalogueDatabaseFactory
    :param new_owner: Function creating an account the catalogue accepts as an owner.
    :type new_owner: OwnerFactory
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The identifier of the book.
    :rtype: BookId
    """
    book_id = await store_book(catalogue_factory(), make_record(), owner=await new_owner(), databases=databases)
    return book_id


class TestBookPlaceRepository:
    """Tests for the place the reader left a book at."""

    async def test_save_stores_a_place_with_every_field(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a place reads back whole, with its canvas position and the page of the strip.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await _store_book(fx_catalogue, fx_new_owner, databases=fx_databases())
        place = make_book_place(page_id=PageId(uuid4()))
        assert (
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(place))
            == place
        )
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) == place

    async def test_a_place_without_a_canvas_position_reads_back_without_one(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a reader who has not zoomed leaves no canvas position, and a reading place keeps its stage.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await _store_book(fx_catalogue, fx_new_owner, databases=fx_databases())
        place = evolve(
            make_book_place(stage=Stage.PAGE_ORDER),
            mode=PlaceMode.READING,
            canvas=None,
            strip_page_id=None,
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(place))
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) == place

    async def test_save_replaces_the_place_of_the_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a second save replaces the first and leaves one place.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await _store_book(fx_catalogue, fx_new_owner, databases=fx_databases())
        first = make_book_place()
        second = evolve(first, stage=Stage.CLEANUP, page_id=PageId(uuid4()), canvas=None)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(first))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(second))
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) == second

    async def test_successive_saves_leave_the_later_place_whole(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the later of two places saved one after the other is stored whole, the mode it shares with the old one too.

        The stored place and the later one agree on the mode, and the earlier one differs from both, so a store that
        wrote only the columns differing from the place it last read would leave the mode of the earlier place.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await _store_book(fx_catalogue, fx_new_owner, databases=fx_databases())
        stored = evolve(make_book_place(), mode=PlaceMode.READING)
        first = evolve(
            stored, mode=PlaceMode.WORKSPACE, page_id=None, canvas=None, updated_at=stored.updated_at + LATER
        )
        second = evolve(
            stored,
            page_id=PageId(uuid4()),
            canvas=CanvasPosition(zoom=1.0, centre_x=0.4348, centre_y=0.5),
            updated_at=stored.updated_at + 2 * LATER,
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(stored))
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) == stored
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(first))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(second))
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) == second

    async def test_save_of_an_older_place_leaves_the_newer_one(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a write stamped before the stored place changes nothing and answers with the stored place.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await _store_book(fx_catalogue, fx_new_owner, databases=fx_databases())
        older = make_book_place()
        newer = evolve(older, stage=Stage.CLEANUP, page_id=PageId(uuid4()), updated_at=older.updated_at + LATER)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(newer))
        assert (
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_places.save(older))
            == newer
        )
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) == newer

    async def test_find_gives_none_for_a_book_not_worked_on(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a book nobody has worked on has no place.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await _store_book(fx_catalogue, fx_new_owner, databases=fx_databases())
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_places.find()) is None
