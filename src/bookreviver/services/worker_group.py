"""Doing one piece of work for each of many items, a few at a time.

A job that goes over the pages or the scans of a book does the same work for each of them, and the work of one does
not wait for the work of another: each cuts or processes its own files in a process of the pool and writes its own
rows in a short transaction. ``WorkerGroup`` runs that work with a few workers, each of which takes the next item when
it has finished its last, so a book is done as fast as the machine allows and no more of it is held in memory than the
workers have in hand.

A job is cancelled by ``JobCancelledError``, which the control of the job raises in the worker that asked it. The group
then cancels the other workers, so the job stops at once and nothing of it is left running.
"""

from typing import TYPE_CHECKING

import anyio
from attrs import field, frozen, validators

from bookreviver.domain.errors import JobCancelledError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence


@frozen
class WorkerGroup:
    """Workers that go over items together, each doing the work of one item at a time.

    :ivar size: Largest number of items worked on at the same time.
    """

    size: int = field(validator=validators.ge(1))

    async def run[ItemT, ResultT](
        self, items: Sequence[ItemT], work: Callable[[ItemT], Awaitable[ResultT]]
    ) -> list[ResultT]:
        """Do the work of every item, and give what each came to in the order of the items.

        The items are taken in their order, so with one worker they are done one after the other.

        :param items: The items to go over.
        :type items: Sequence[ItemT]
        :param work: The work of one item.
        :type work: Callable[[ItemT], Awaitable[ResultT]]
        :returns: What the work of each item returned, in the order of the items.
        :rtype: list[ResultT]
        :raises JobCancelledError: If the job was cancelled and no worker failed.
        :raises BaseException: The first fault a worker raised, which stops the others.
        """
        results: dict[int, ResultT] = {}
        pending = iter(enumerate(items))

        async def worker() -> None:
            """Take items from the shared iterator until none is left, each one done before the next is taken."""
            for index, item in pending:
                results[index] = await work(item)

        try:
            async with anyio.create_task_group() as group:
                for _ in range(min(self.size, len(items))):
                    group.start_soon(worker)
        except BaseExceptionGroup as errors:
            # The group of anyio holds the fault of every worker, in the order the workers finished, whether the worker
            # raised it before the cancellation of the group reached it or while unwinding from it. A cancellation is
            # only the job's own state, so it must not hide a fault, which would end the job as cancelled unlogged.
            _, faults = errors.split(JobCancelledError)
            raise (faults or errors).exceptions[0] from errors
        return [results[index] for index in range(len(items))]
