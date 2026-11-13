"""The book of two sources with scans and a page that tests of several levels start from."""

from typing import TYPE_CHECKING

import pytest

from tests.harness.builders.books import commit_book

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.accounts import Actor
    from tests.harness.builders.books import Book
    from tests.harness.builders.domain import BookOpener


@pytest.fixture
async def fx_book(
    fx_database: SqlDatabase,
    fx_actor: Actor,
    fx_open_book: BookOpener,
) -> Book:
    """Commit and store a book of the acting account.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_actor: Account the service acts for.
    :type fx_actor: Actor
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_open_book, fx_actor)


@pytest.fixture
async def fx_strangers_book(
    fx_database: SqlDatabase,
    fx_open_book: BookOpener,
) -> Book:
    """Commit and store a book of an account that acts nowhere in the test.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_open_book, None)
