"""Holding a book, so a test can race another change of the same book against one that has not committed.

A change of the port is a synchronous function that returns to the event loop only when it has committed, so a test
cannot suspend it halfway and start a second change beside it. The test holds a book instead: the SQL adapter runs the
work of a change in a thread of its own, which ``hold_book_in_thread`` blocks, and SQLite has one writer, so while that
change is held every other change of every book waits behind it.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import UnitOfWork

# The longest a blocked change holds its thread when a test fails before it lets the change go
HOLD_LIMIT_SECONDS: float = 30.0


@asynccontextmanager
async def hold_book_in_thread(uow: UnitOfWork, project_id: ProjectId | None = None) -> AsyncIterator[None]:
    """Hold a book by a change whose work blocks the thread it runs in until the block ends.

    SQLite has one writer, so every other change waits behind the held one, whatever book it changes.

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
