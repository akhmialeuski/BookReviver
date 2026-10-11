"""Tests for the in-memory unit of work where it cannot share the contract suite with a database.

A database with one write lock, such as SQLite, makes every block wait for any other that writes, so the contract
cannot require that changes of two books run at the same time. The in-memory adapter holds one lock for each book,
which only this adapter can show.
"""

import pytest

from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from tests.helpers.builders import make_project, new_account_id
from tests.helpers.seeding import store_project

pytestmark = pytest.mark.anyio

# Short enough to fail a test that waits, and long enough that nothing else makes it fail
WAIT_SECONDS: float = 0.2


class TestChangeBook:
    """Tests for InMemoryUnitOfWork.change_book()."""

    async def test_changes_of_two_books_do_not_wait_for_each_other(self) -> None:
        """Verify a block of one book opens at once while a block of another book is open.

        Both blocks are opened by one task, one inside the other, so a block that waited for the lock of the other
        book would wait for the limit and raise ``BookBusyError`` instead of entering.
        """
        database = InMemoryDatabase(wait_seconds=WAIT_SECONDS)
        owner_id = new_account_id()
        first_book, second_book = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        await store_project(InMemoryUnitOfWork(database), first_book)
        await store_project(InMemoryUnitOfWork(database), second_book)
        first, second = InMemoryUnitOfWork(database), InMemoryUnitOfWork(database)
        async with first.change_book(first_book.id), second.change_book(second_book.id) as project:
            assert project == second_book
