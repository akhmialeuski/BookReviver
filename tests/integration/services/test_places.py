"""Tests for the use cases of the place an account left a book at."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.domain.accounts import Actor
from bookreviver.services.places import PlaceService
from tests.harness.builders.domain import make_record, new_account_id
from tests.harness.builders.seeding import commit_record

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from tests.harness.adapters.database import CatalogueDatabaseFactory
    from tests.harness.builders.domain import BookOpener

pytestmark = pytest.mark.anyio


def _service() -> PlaceService:
    """Build the place service for one request.

    :returns: The service over a new catalogue database.
    :rtype: PlaceService
    """
    return PlaceService()


class TestPlaceService:
    """Tests for the place service."""

    async def test_a_book_not_worked_on_has_no_place(
        self, fx_database: SqlDatabase, fx_catalogue: CatalogueDatabaseFactory, fx_open_book: BookOpener
    ) -> None:
        """Verify the place of a new book is none.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor = Actor(account_id=new_account_id())
        book_id = await commit_record(
            fx_database, fx_catalogue(), make_record(), owner=actor.account_id, databases=fx_open_book.databases
        )
        places = _service()
        assert await places.find(fx_open_book(actor, book_id)) is None
