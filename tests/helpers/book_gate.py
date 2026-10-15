"""Holding the lock of a book, so a test can race another change of the same book against one that has not committed.

A change of the port is a synchronous function that returns to the event loop only when it has committed, so a test
cannot suspend it halfway and start a second change beside it. The test holds the lock the changes of a book take
instead, in the way each adapter allows: the in-memory adapter keeps the lock in its database, where the test takes it,
and the SQL adapter runs the work in a thread that the test blocks.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import UnitOfWork

# The longest a blocked change holds its thread when a test fails before it lets the change go
HOLD_LIMIT_SECONDS: float = 30.0


@asynccontextmanager
async def hold_book(database: InMemoryDatabase, project_id: ProjectId | None = None) -> AsyncIterator[None]:
    """Hold the lock of a book of the in-memory database, which every change of that book waits for.

    :param database: In-memory database whose lock is taken.
    :type database: InMemoryDatabase
    :param project_id: Project whose book is held, or None for the lock of the changes outside the content of a book.
    :type project_id: ProjectId | None
    :returns: Iterator yielding once the lock is held, and releasing it when the block ends.
    :rtype: AsyncIterator[None]
    """
    lock = database.change_lock if project_id is None else database.book_lock(project_id)
    async with lock:
        yield


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
