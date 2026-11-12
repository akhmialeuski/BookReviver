"""Tests for the workers that go over the items of a job a few at a time."""

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import ConflictError, JobCancelledError
from bookreviver.services.worker_group import WorkerGroup

pytestmark = pytest.mark.anyio

ITEMS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
WORKERS: int = 3
FAULTY_ITEM: int = 2
FAULT: str = 'The item cannot be done.'


class Watch:
    """Counts the items in work at the same time, and yields to the other workers inside the work of each.

    :ivar most: Largest number of items that were in work together.
    :ivar begun: The items, in the order their work began.
    """

    def __init__(self) -> None:
        """Start with nothing in work."""
        self.most = 0
        self.begun: list[int] = []
        self._now = 0

    async def double(self, item: int) -> int:
        """Do the work of one item, which gives the loop to the other workers once.

        :param item: The item.
        :type item: int
        :returns: Twice the item.
        :rtype: int
        """
        self.begun.append(item)
        self._now += 1
        self.most = max(self.most, self._now)
        await anyio.lowlevel.checkpoint()
        self._now -= 1
        return item * 2


class TestRun:
    """Tests for WorkerGroup.run."""

    async def test_results_come_in_the_order_of_the_items_whatever_worker_made_them(self) -> None:
        """Verify what each item came to is given at the place of the item."""
        assert await WorkerGroup(WORKERS).run(ITEMS, Watch().double) == [item * 2 for item in ITEMS]

    @pytest.mark.parametrize(('size', 'together'), [(1, 1), (WORKERS, WORKERS), (len(ITEMS) + 1, len(ITEMS))])
    async def test_no_more_items_are_in_work_than_the_group_has_workers(self, size: int, together: int) -> None:
        """Verify the group works on as many items at once as it has workers, and on no more than there are items.

        :param size: Number of workers.
        :type size: int
        :param together: Largest number of items in work at the same time.
        :type together: int
        """
        watch = Watch()
        await WorkerGroup(size).run(ITEMS, watch.double)
        expect(watch.most == together)
        expect(sorted(watch.begun) == list(ITEMS))
        assert_expectations()

    async def test_one_worker_does_the_items_one_after_the_other(self) -> None:
        """Verify a group of one keeps the order of the items, as a job that must not overlap its work needs."""
        watch = Watch()
        await WorkerGroup(1).run(ITEMS, watch.double)
        assert watch.begun == list(ITEMS)

    async def test_no_items_is_no_work(self) -> None:
        """Verify a group given nothing returns nothing and starts no worker."""
        assert await WorkerGroup(WORKERS).run((), Watch().double) == []

    async def test_the_fault_of_an_item_stops_the_group_and_is_raised_as_it_is(self) -> None:
        """Verify the error of one item reaches the caller unwrapped, and the items after it are not begun."""
        begun: list[int] = []

        async def work(item: int) -> int:
            """Fail on one item.

            :param item: The item.
            :type item: int
            :returns: The item.
            :rtype: int
            :raises ConflictError: On the faulty item.
            """
            begun.append(item)
            await anyio.lowlevel.checkpoint()
            if item == FAULTY_ITEM:
                raise ConflictError(FAULT)
            return item

        with pytest.raises(ConflictError, match=FAULT):
            await WorkerGroup(1).run(ITEMS, work)
        assert begun == list(ITEMS[: FAULTY_ITEM + 1])

    async def test_a_cancelled_job_stops_the_group_with_the_cancellation(self) -> None:
        """Verify the cancellation the control of a job raises in one worker ends the group as cancelled."""

        async def work(item: int) -> int:
            """Learn of the cancellation on one item.

            :param item: The item.
            :type item: int
            :returns: The item.
            :rtype: int
            :raises JobCancelledError: On the item whose worker asked the control.
            """
            await anyio.lowlevel.checkpoint()
            if item == FAULTY_ITEM:
                raise JobCancelledError
            return item

        with pytest.raises(JobCancelledError):
            await WorkerGroup(WORKERS).run(ITEMS, work)

    def test_a_group_has_at_least_one_worker(self) -> None:
        """Verify a group without a worker is refused, since it would do nothing and return."""
        with pytest.raises(ValueError, match='size'):
            WorkerGroup(0)
