"""Tests for the probe of the event-loop lag, which records one ``loop.lag`` span for each tick it sleeps.

The probe runs in a task group of the test, over a recording tracer that sets an event when a tick has started. A tick
starts as the one before it ends, so the next tick starting means the one before it has its lag. The only deliberate
delay is one blocking ``time.sleep``, which stalls the loop while the probe is asleep.
"""

import time

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.app.tracing import LoopLagProbe
from bookreviver.domain.spans import SpanAttribute, SpanName
from tests.helpers.tracers import AskingTracer

pytestmark = pytest.mark.anyio

INTERVAL_SECONDS: float = 0.01
# Ten intervals, so the tick that sleeps through the block ends about 90 ms late
BLOCKED_SECONDS: float = 0.1
# Below the 90 ms of the block, so that a coarse timer does not fail the test
LATE_TICK_MS: float = 80.0


def block_the_loop(seconds: float) -> None:
    """Stall the event loop for ``seconds`` the way a synchronous call in a handler does.

    :param seconds: How long nothing else on the loop runs.
    :type seconds: float
    """
    time.sleep(seconds)


class TestLoopLagProbe:
    """Tests for the ticks of the probe."""

    async def test_blocked_loop_is_recorded_as_the_lag_of_the_tick_it_delayed(self) -> None:
        """Verify a tick asleep while the loop is blocked ends late by about the block, and its span is a root.

        The probe is known to be asleep once its first tick has started. The second wait is set before the block, so
        the tick that starts after the block is the one that follows the late one, which has ended and holds its lag.
        The task group is opened inside a span, which its task copies as its current span, so a tick that has no
        parent has asked for none.
        """
        tracer = AskingTracer()
        probe = LoopLagProbe(tracer=tracer, interval_seconds=INTERVAL_SECONDS)
        asleep = tracer.ask_after(1, name=SpanName.LOOP_LAG)
        with tracer.span(SpanName.JOB):
            async with anyio.create_task_group() as group:
                group.start_soon(probe.run)
                await asleep.wait()
                resumed = tracer.ask_after(1, name=SpanName.LOOP_LAG)
                block_the_loop(BLOCKED_SECONDS)
                await resumed.wait()
                group.cancel_scope.cancel()
        ticks = tracer.named(SpanName.LOOP_LAG)
        lags = [float(tick.attributes[SpanAttribute.LAG_MS]) for tick in ticks]
        expect(len(ticks) > 0, 'No tick was recorded.')
        expect(max(lags, default=0.0) >= LATE_TICK_MS, f'The lags were {lags}.')
        expect(all(lag >= 0.0 for lag in lags))
        expect(all(tick.parent is None for tick in ticks))
        assert_expectations()
