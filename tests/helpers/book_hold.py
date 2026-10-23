"""Holding a book, so a test can race another change of the same book against one that has not committed.

A change of the port is a synchronous function that returns to the event loop only when it has committed, so a test
cannot suspend it halfway and start a second change beside it. The test holds a book instead: the SQL adapter runs the
work of a change in a thread of its own, which ``hold_book_in_thread`` blocks. A book and the catalogue each have a
file and a writer of their own, so while that change is held every other change of the same store waits behind it, and a
change of another store does not.
"""

import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import UnitOfWork

# The longest a blocked change holds its thread when a test fails before it lets the change go
HOLD_LIMIT_SECONDS: float = 30.0


@asynccontextmanager
async def hold_book_in_thread(uow: UnitOfWork, book_id: BookId | None = None) -> AsyncIterator[None]:
    """Hold a book by a change whose work blocks the thread it runs in until the block ends.

    Every other change of the same store waits behind the held one. A change of another book, or of the catalogue when a
    book is held, runs at once.

    :param uow: Unit of work whose change holds the book.
    :type uow: UnitOfWork
    :param book_id: Project whose book is held, or None for the catalogue.
    :type book_id: BookId | None
    :returns: Iterator yielding once the work of the change has started, and letting it commit when the block ends.
    :rtype: AsyncIterator[None]
    """
    inside, release = anyio.Event(), threading.Event()

    def block(*_arguments: object) -> None:
        """Tell the test that the change has begun, then wait in this thread until the test lets it go.

        The work runs in a worker thread of ``anyio.to_thread``, so it reaches the event loop through
        ``anyio.from_thread``.

        :param _arguments: What the unit of work hands its work, which this work does not need.
        :type _arguments: object
        """
        anyio.from_thread.run_sync(inside.set)
        release.wait(timeout=HOLD_LIMIT_SECONDS)

    async with anyio.create_task_group() as tasks:
        if book_id is None:
            tasks.start_soon(uow.change, block)
        else:
            tasks.start_soon(uow.change_book, book_id, block)
        await inside.wait()
        try:
            yield
        finally:
            release.set()
