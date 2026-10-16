"""The turn of a block of the persistence port at a lock, given in the order asked and refused after the wait limit.

Both adapters order their blocks the same way: the in-memory adapter at the semaphore of a book, the SQLAlchemy adapter
at the semaphore of one that is the write queue of its database. ``anyio.Semaphore`` wakes its waiters first in, first
out, because it keeps them in a deque, which anyio 4.15 shows in ``anyio/_backends/_asyncio.py``. An ``anyio.Lock`` is
no choice here: a lock belongs to the task that took it and refuses that task a second time at once, while the holder
of the semaphore is the unit of work, as a database holds its lock for a connection.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

import anyio

from bookreviver.domain.errors import BookBusyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def hold_in_turn(lock: anyio.Semaphore, *, wait_seconds: float) -> AsyncIterator[None]:
    """Wait for a single-holder lock in the order asked, and hold it until the block ends.

    Only the wait is limited, never the holding.

    :param lock: Semaphore of one token, which the block takes.
    :type lock: anyio.Semaphore
    :param wait_seconds: How long the block waits for the lock before it gives up.
    :type wait_seconds: float
    :returns: Iterator yielding once the lock is held, and releasing it when the block ends.
    :rtype: AsyncIterator[None]
    :raises BookBusyError: If the lock was not given within the wait limit.
    """
    async with AsyncExitStack() as held:
        try:
            with anyio.fail_after(wait_seconds):
                await held.enter_async_context(lock)
        except TimeoutError as error:
            raise BookBusyError from error
        yield
