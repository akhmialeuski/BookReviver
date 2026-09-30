"""Contract of the JobQueue port, run against every adapter listed in ``QueueAdapter``."""

from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from bookreviver.domain.ids import ProjectId
from tests.helpers.builders import make_job
from tests.helpers.job_queues import open_queue_under_test

if TYPE_CHECKING:
    from tests.helpers.job_queues import QueueAdapter

pytestmark = pytest.mark.anyio

JOB_COUNT: int = 3
PROJECT_ID: ProjectId = ProjectId(UUID(int=1))


class TestJobQueue:
    """Contract of JobQueue.enqueue()."""

    async def test_every_job_reaches_its_consumer_once_in_order(self, fx_queue_adapter: QueueAdapter) -> None:
        """Verify each enqueued job's identifier reaches the consumer exactly once, in the order of enqueueing.

        :param fx_queue_adapter: Adapter under test.
        :type fx_queue_adapter: QueueAdapter
        """
        jobs = [make_job(project_id=PROJECT_ID, minutes=minute) for minute in range(JOB_COUNT)]
        async with open_queue_under_test(fx_queue_adapter) as under_test:
            for job in jobs:
                await under_test.queue.enqueue(job)
        assert under_test.delivered() == [job.id for job in jobs]
