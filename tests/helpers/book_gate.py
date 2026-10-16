"""Holding a book, so a test can race another change of the same book against one that has not committed.

A change of the port is a synchronous function that returns to the event loop only when it has committed, so a test
cannot suspend it halfway and start a second change beside it. The test holds a book instead, in the way each adapter
allows. The in-memory adapter has no lock to hold, so the test gates its units of work: a ``BookGate`` keeps one
single-holder semaphore for each book and one for the changes outside books, and a ``GatedUnitOfWork`` takes the
semaphore of its change in turn, as the write queue of the SQL adapter does, before it hands the change to the unit of
work it wraps. A test holds a semaphore with ``BookGate.hold``, and every gated change of that book waits behind it. The
SQL adapter runs the work in a thread, which ``hold_book_in_thread`` blocks.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

import anyio

from bookreviver.adapters.persistence.sqlalchemy.database import hold_in_turn
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import Repositories

# The longest a blocked change holds its thread when a test fails before it lets the change go
HOLD_LIMIT_SECONDS: float = 30.0


class BookGate:
    """The semaphores a test holds to keep the changes of a book waiting: one for each book, one outside books."""

    def __init__(self) -> None:
        """Start with the semaphore of the changes outside books and none for a book, which is made when first asked."""
        self._outside_books = anyio.Semaphore(1)
        self._books: dict[ProjectId, anyio.Semaphore] = {}

    def semaphore(self, project_id: ProjectId | None = None) -> anyio.Semaphore:
        """Return the single-holder semaphore of a book, or of the changes outside the content of a book.

        :param project_id: Project whose book is changed, or None for a change outside the content of a book.
        :type project_id: ProjectId | None
        :returns: The semaphore every gated change of that book takes.
        :rtype: anyio.Semaphore
        """
        if project_id is None:
            return self._outside_books
        return self._books.setdefault(project_id, anyio.Semaphore(1))

    @asynccontextmanager
    async def hold(self, project_id: ProjectId | None = None) -> AsyncIterator[None]:
        """Hold the semaphore of a book, which every gated change of that book waits for.

        :param project_id: Project whose book is held, or None for the changes outside the content of a book.
        :type project_id: ProjectId | None
        :returns: Iterator yielding once the semaphore is held, and releasing it when the block ends.
        :rtype: AsyncIterator[None]
        """
        async with self.semaphore(project_id):
            yield


class GatedUnitOfWork(UnitOfWork):
    """A unit of work whose changes take the semaphore of a gate before they reach the unit of work it wraps.

    :ivar gate: Gate whose semaphores the changes take, which a test holds to keep them waiting.
    """

    def __init__(self, uow: UnitOfWork, gate: BookGate) -> None:
        """Wrap a unit of work behind a gate.

        :param uow: Unit of work that runs the work, which the gate only delays.
        :type uow: UnitOfWork
        :param gate: Gate whose semaphores the changes take, shared by every gated unit of work of the test.
        :type gate: BookGate
        """
        self._uow = uow
        self.gate = gate

    @override
    async def read[T](self, work: Callable[[Repositories], T]) -> T:
        """Run ``work`` on the wrapped unit of work at once, since a read takes no semaphore.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Repositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._uow.read(work)

    @override
    async def change[T](self, work: Callable[[Repositories], T]) -> T:
        """Wait in turn for the semaphore of the changes outside books, then run ``work`` on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Repositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        async with hold_in_turn(self.gate.semaphore(), wait_seconds=DEFAULT_CHANGE_WAIT_SECONDS):
            return await self._uow.change(work)

    @override
    async def change_book[T](self, project_id: ProjectId, work: Callable[[Repositories, Project], T]) -> T:
        """Wait in turn for the semaphore of the book, then run ``work`` on the wrapped unit of work.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project read at its start.
        :type work: Callable[[Repositories, Project], T]
        :returns: What the work returns.
        :rtype: T
        """
        async with hold_in_turn(self.gate.semaphore(project_id), wait_seconds=DEFAULT_CHANGE_WAIT_SECONDS):
            return await self._uow.change_book(project_id, work)


@asynccontextmanager
async def hold_book_in_thread(uow: UnitOfWork, project_id: ProjectId | None = None) -> AsyncIterator[None]:
    """Hold a book by a change whose work blocks the thread it runs in until the block ends.

    It suits an adapter that runs the work of a change in a thread of its own, which the event loop can leave blocked.

    :param uow: Unit of work whose change holds the book.
    :type uow: UnitOfWork
    :param project_id: Project whose book is held, or None for the changes outside the content of a book.
    :type project_id: ProjectId | None
    :returns: Iterator yielding once the work of the change has started, and letting it commit when the block ends.
    :rtype: AsyncIterator[None]
    """
    loop = asyncio.get_running_loop()
    inside, release = anyio.Event(), threading.Event()

    def block(*_arguments: object) -> None:
        """Tell the test that the change has begun, then wait in this thread until the test lets it go.

        :param _arguments: What the unit of work hands its work, which this work does not need.
        :type _arguments: object
        """
        loop.call_soon_threadsafe(inside.set)
        release.wait(timeout=HOLD_LIMIT_SECONDS)

    async with anyio.create_task_group() as tasks:
        if project_id is None:
            tasks.start_soon(uow.change, block)
        else:
            tasks.start_soon(uow.change_book, project_id, block)
        await inside.wait()
        try:
            yield
        finally:
            release.set()
