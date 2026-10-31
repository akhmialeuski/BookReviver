"""Looking at a book while a job is inside a task, by holding a step of the fake processor in its worker thread.

A job stops only between tasks, so the moments worth testing, such as a cancel that lands while a task is under way, need
a task that stays under way until the test says otherwise. The steps of the kit run in worker threads, so the hold is a
``threading.Event`` the thread waits on, and the test goes on in the event loop meanwhile.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from tests.harness.builders.processing import ProcessingKit

# How long a test waits for a step to reach the processor, which is the time of a thread to start
STEP_ENTRY_LIMIT_S: float = 10.0


@asynccontextmanager
async def steps_held(kit: ProcessingKit, *, after: int = 0) -> AsyncGenerator[None]:
    """Run the jobs put on the board of the kit, with a step of the fake processor held inside its thread.

    The block starts once the step is inside the processor, so the job that owns it is running and in the middle of a
    task. Leaving the block lets the step go and waits until the board has run everything it holds, so what a test reads
    after the block is the end of the story.

    :param kit: What the processing services of the test share, whose board has the jobs to run put on it.
    :type kit: ProcessingKit
    :param after: How many steps run freely before the one that is held.
    :type after: int
    :returns: Generator yielding while the step is held.
    :rtype: AsyncGenerator[None]
    """
    release = kit.fake.hold(after=after)
    async with anyio.create_task_group() as group:
        try:
            group.start_soon(kit.board.run_queued)
            assert await anyio.to_thread.run_sync(kit.fake.entered.wait, STEP_ENTRY_LIMIT_S)
            yield
        finally:
            release.set()
