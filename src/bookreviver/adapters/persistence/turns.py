"""The turn of a block of the persistence port at a lock, given in the order asked and refused after the wait limit.

Both adapters order their blocks the same way: the in-memory adapter at the semaphore of a book, the SQLAlchemy adapter
at the capacity limiter of one that is the write queue of its database. Both wake their waiters first in, first out:
``anyio.Semaphore`` keeps them in a deque and ``anyio.CapacityLimiter`` in an ordered dictionary whose oldest entry is
served first, which anyio 4.15 shows in ``anyio/_backends/_asyncio.py``. An ``anyio.Lock`` is no choice here: a lock
belongs to the task that took it and refuses that task a second time at once, while the holder of the semaphore is the
unit of work, as a database holds its lock for a connection. A capacity limiter has a task for a borrower and refuses
a second token to the same task, which cannot happen, because the work of a block holds repositories and not the unit of
work, so a task never opens a block inside a block.
"""

from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

import anyio

from bookreviver.domain.errors import BookBusyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The locks a block waits at, each of which hands its tokens to the waiters in the order they asked
type TurnLock = anyio.Semaphore | anyio.CapacityLimiter


@asynccontextmanager
async def hold_in_turn(lock: TurnLock, *, wait_seconds: float) -> AsyncIterator[None]:
    """Wait for a single-holder lock in the order asked, and hold it until the block ends.

    Only the wait is limited, never the holding.

    :param lock: Semaphore of one token, or capacity limiter of one, which the block takes.
    :type lock: TurnLock
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
