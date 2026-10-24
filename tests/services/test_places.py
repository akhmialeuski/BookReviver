"""Tests for the use cases of the place an account left a book at."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.domain.entities import Actor
from bookreviver.services.places import PlaceService
from tests.helpers.builders import make_record, new_account_id
from tests.helpers.seeding import commit_record

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio


def _service() -> PlaceService:
    """Build the place service for one request.

    :returns: The service over a new unit of work.
    :rtype: PlaceService
    """
    return PlaceService()


class TestPlaceService:
    """Tests for the place service."""

    async def test_a_book_not_worked_on_has_no_place(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_open_book: BookOpener
    ) -> None:
        """Verify the place of a new book is none.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor = Actor(account_id=new_account_id())
        book_id = await commit_record(
            fx_database, fx_uow(), make_record(), owner=actor.account_id, databases=fx_open_book.databases
        )
        places = _service()
        assert await places.find(fx_open_book(actor, book_id)) is None
