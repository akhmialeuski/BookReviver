"""Tests for the in-memory unit of work where it cannot share the contract suite with a database.

A database with one write lock, such as SQLite, makes every change wait for any other that writes, so the contract
cannot require that changes of two books run at the same time. The in-memory adapter holds one lock for each book,
which only this adapter can show.
"""

import pytest

from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from tests.helpers.book_gate import hold_book
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.seeding import store_project

pytestmark = pytest.mark.anyio

# Short enough to fail a test that waits, and long enough that nothing else makes it fail
WAIT_SECONDS: float = 0.2


class TestLocks:
    """Tests for the locks of InMemoryUnitOfWork: one for each book and one for the changes outside books."""

    async def test_change_of_one_book_does_not_wait_for_another_book(self) -> None:
        """Verify a change of one book runs at once while the lock of another book is held.

        A change that waited for the lock of the other book would wait for the limit and raise ``BookBusyError``
        instead of running.
        """
        database = InMemoryDatabase()
        owner_id = new_account_id()
        first_book, second_book = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        await store_project(InMemoryUnitOfWork(database), first_book)
        await store_project(InMemoryUnitOfWork(database), second_book)
        uow = InMemoryUnitOfWork(database, wait_seconds=WAIT_SECONDS)
        async with hold_book(database, first_book.id):
            project = await uow.change_book(second_book.id, lambda _repos, project: project)
        assert project == second_book

    async def test_change_outside_a_book_does_not_wait_for_a_book(self) -> None:
        """Verify a change outside the content of a book runs at once while the lock of a book is held."""
        database = InMemoryDatabase()
        book = make_project(owner_id=new_account_id())
        await store_project(InMemoryUnitOfWork(database), book)
        job = make_job(project_id=book.id)
        uow = InMemoryUnitOfWork(database, wait_seconds=WAIT_SECONDS)
        async with hold_book(database, book.id):
            stored = await uow.change(lambda repos: repos.jobs.add(job))
        assert stored == job

    async def test_change_of_a_book_does_not_wait_for_the_changes_outside_books(self) -> None:
        """Verify a change of a book runs at once while the lock of the changes outside books is held."""
        database = InMemoryDatabase()
        book = make_project(owner_id=new_account_id())
        await store_project(InMemoryUnitOfWork(database), book)
        uow = InMemoryUnitOfWork(database, wait_seconds=WAIT_SECONDS)
        async with hold_book(database):
            project = await uow.change_book(book.id, lambda _repos, project: project)
        assert project == book
