"""Tests for the one book an account works on: opening another book or going back to the library lets go of it.

What lets go of a book is the registry of the databases of the books, so the tests record the books it is asked to
release, and leave the release itself to its own tests.
"""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.api.routing import API_PREFIX
from tests.harness.builders.domain import directory_of, make_record
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookDirectoryName, BookId
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = f'{API_PREFIX}/books'
BOOK_PATH: str = f'{API_PREFIX}/books/{{book_id}}'
BOOK_COUNT: int = 2


@pytest.fixture
async def fx_two_books(
    fx_catalogue: CatalogueDatabaseFactory, fx_databases: BookDatabasesFactory, fx_actor: Actor
) -> list[BookId]:
    """Store two books of the account.

    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :param fx_actor: Account that owns the books, which every request acts as.
    :type fx_actor: Actor
    :returns: The identifiers of the books.
    :rtype: list[BookId]
    """
    return [
        await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())
        for _ in range(BOOK_COUNT)
    ]


@pytest.fixture
def fx_released(monkeypatch: pytest.MonkeyPatch) -> list[BookDirectoryName]:
    """Record the books the registry is asked to release, which it then releases as it does.

    :param monkeypatch: Fixture that restores the registry after the test.
    :type monkeypatch: pytest.MonkeyPatch
    :returns: The directory names of the books asked for, in the order asked.
    :rtype: list[BookDirectoryName]
    """
    released: list[BookDirectoryName] = []
    release = BookRegistry.release

    async def recording_release(registry: BookRegistry, dir_name: BookDirectoryName) -> None:
        """Record the book, then release it.

        :param registry: The registry.
        :type registry: BookRegistry
        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        """
        released.append(dir_name)
        await release(registry, dir_name)

    monkeypatch.setattr(BookRegistry, 'release', recording_release)
    return released


class TestOneOpenBook:
    """Tests for the book the account works on, which is let go of when the account leaves it."""

    async def test_opening_another_book_lets_go_of_the_first(
        self, fx_client: httpx.AsyncClient, fx_two_books: list[BookId], fx_released: list[BookDirectoryName]
    ) -> None:
        """Verify the first book is released when the second is opened, and reading the open book releases nothing.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_two_books: The two books of the account.
        :type fx_two_books: list[BookId]
        :param fx_released: The books the registry was asked to release.
        :type fx_released: list[BookDirectoryName]
        """
        first, second = fx_two_books
        for book_id in (first, first, second, second):
            (await fx_client.get(BOOK_PATH.format(book_id=book_id))).raise_for_status()

        assert fx_released == [directory_of(first)]

    async def test_going_back_to_the_library_lets_go_of_the_book_and_of_the_books_it_shows(
        self, fx_client: httpx.AsyncClient, fx_two_books: list[BookId], fx_released: list[BookDirectoryName]
    ) -> None:
        """Verify the library releases the book the account left, and each book it read to show, once read.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_two_books: The two books of the account.
        :type fx_two_books: list[BookId]
        :param fx_released: The books the registry was asked to release.
        :type fx_released: list[BookDirectoryName]
        """
        (await fx_client.get(BOOK_PATH.format(book_id=fx_two_books[0]))).raise_for_status()

        (await fx_client.get(BOOKS_PATH)).raise_for_status()

        assert sorted(fx_released) == sorted([directory_of(fx_two_books[0]), *map(directory_of, fx_two_books)])
