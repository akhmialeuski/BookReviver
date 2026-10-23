"""Contract of the persistence port of the place the reader left a book at.

Every test runs against the SQL adapter, so it keeps the promises of the port: one place for each book, a
replacement on every save that is whole, and the removal of the place with the book.
"""

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve

from bookreviver.domain.enums import PlaceMode, Stage
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import CanvasPosition
from tests.helpers.builders import make_book_place, make_record
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.ids import BookId
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# How much later one write of a place is stamped than another
LATER: timedelta = timedelta(minutes=5)


async def _store_book(uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory) -> BookId:
    """Store a book of a new account.

    :param uow_factory: Function opening a new unit of work over the catalogue and the books of the test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the catalogue accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: The identifier of the project of the book.
    :rtype: BookId
    """
    book_record = make_record()
    await store_book(uow_factory(), book_record, owner=await new_owner())
    return book_record.id


class TestBookPlaceRepository:
    """Tests for the place the reader left a book at."""

    async def test_save_stores_a_place_with_every_field(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a place reads back whole, with its canvas position and the page of the strip.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        book_id = await _store_book(fx_uow, fx_new_owner)
        place = make_book_place(page_id=PageId(uuid4()))
        assert await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(place)) == place
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) == place

    async def test_a_place_without_a_canvas_position_reads_back_without_one(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a reader who has not zoomed leaves no canvas position, and a reading place keeps its stage.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        book_id = await _store_book(fx_uow, fx_new_owner)
        place = evolve(
            make_book_place(stage=Stage.PAGE_ORDER),
            mode=PlaceMode.READING,
            canvas=None,
            strip_page_id=None,
        )
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(place))
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) == place

    async def test_save_replaces_the_place_of_the_book(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second save replaces the first and leaves one place.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        book_id = await _store_book(fx_uow, fx_new_owner)
        first = make_book_place()
        second = evolve(first, stage=Stage.CLEANUP, page_id=PageId(uuid4()), canvas=None)
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(first))
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(second))
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) == second

    async def test_successive_saves_leave_the_later_place_whole(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the later of two places saved one after the other is stored whole, the mode it shares with the old one too.

        The stored place and the later one agree on the mode, and the earlier one differs from both, so a store that
        wrote only the columns differing from the place it last read would leave the mode of the earlier place.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        book_id = await _store_book(fx_uow, fx_new_owner)
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
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(stored))
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) == stored
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(first))
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(second))
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) == second

    async def test_save_of_an_older_place_leaves_the_newer_one(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a write stamped before the stored place changes nothing and answers with the stored place.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        book_id = await _store_book(fx_uow, fx_new_owner)
        older = make_book_place()
        newer = evolve(older, stage=Stage.CLEANUP, page_id=PageId(uuid4()), updated_at=older.updated_at + LATER)
        await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(newer))
        assert await fx_uow().change_book(book_id, lambda repos, _book_record: repos.book_places.save(older)) == newer
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) == newer

    async def test_find_gives_none_for_a_book_not_worked_on(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a book nobody has worked on has no place.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        book_id = await _store_book(fx_uow, fx_new_owner)
        assert await fx_uow().read_book(book_id, lambda repos: repos.book_places.find()) is None
