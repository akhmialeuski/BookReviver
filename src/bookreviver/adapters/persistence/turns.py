"""The turn of a block of the persistence port at a lock, given in the order asked and refused after the wait limit.

Both adapters order their blocks the same way: the in-memory adapter at the semaphore of a book, the SQLAlchemy adapter
at the write queue of its SQLite database. The lock is a single-holder ``anyio.Semaphore``, whose waiters are woken
first in, first out, and not an ``anyio.Lock``: a lock belongs to the task that took it and refuses that task a second
time at once, while the holder here is the unit of work, as a database holds its lock for a connection. A second unit
of work therefore waits for the wait limit whichever task opens it.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

import anyio

from bookreviver.domain.errors import BookBusyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def hold_in_turn(lock: anyio.Semaphore, *, wait_seconds: float) -> AsyncIterator[None]:
    """Wait for a single-holder semaphore in the order asked, and hold it until the block ends.

    Only the wait is limited, never the holding.

    :param lock: Single-holder semaphore the block takes.
    :type lock: anyio.Semaphore
    :param wait_seconds: How long the block waits for the semaphore before it gives up.
    :type wait_seconds: float
    :returns: Iterator yielding once the semaphore is held, and releasing it when the block ends.
    :rtype: AsyncIterator[None]
    :raises BookBusyError: If the semaphore was not given within the wait limit.
    """
    async with AsyncExitStack() as held:
        try:
            with anyio.fail_after(wait_seconds):
                await held.enter_async_context(lock)
        except TimeoutError as error:
            raise BookBusyError from error
        yield
