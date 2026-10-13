"""Tests for the processing provider: the executor of the steps the container builds."""

import os
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.execution.process_pool import ProcessPoolStepExecutor
from bookreviver.app.container import build_container
from bookreviver.ports.processing import StepExecutor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

STEP_PROCESSES: int = 4
PREVIEW_STEP_PROCESSES: int = 1
TIMEOUT_S: float = 30.0


class TestStepExecutorProvider:
    """Tests for ProcessingProvider.step_executor()."""

    @pytest.fixture
    async def fx_container(self, fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
        """Build a container whose pool serves no pool of workers, so its processes build an empty catalogue.

        :param fx_settings: Settings of the suite.
        :type fx_settings: Settings
        :returns: Iterator yielding the container and closing it afterwards, which a test that closed it already allows.
        :rtype: AsyncIterator[AsyncContainer]
        """
        processing = fx_settings.processing.model_copy(
            update={
                'step_processes': STEP_PROCESSES,
                'preview_step_processes': PREVIEW_STEP_PROCESSES,
                'worker_pools': frozenset(),
            }
        )
        container = build_container(fx_settings.model_copy(update={'processing': processing}))
        try:
            yield container
        finally:
            await container.close()

    async def test_previews_get_their_own_processes_and_full_runs_get_the_rest(
        self, fx_container: AsyncContainer
    ) -> None:
        """Verify the pool is started with the processes of the settings, split between full runs and previews.

        :param fx_container: Container of the test.
        :type fx_container: AsyncContainer
        """
        with anyio.fail_after(TIMEOUT_S):
            executor = await fx_container.get(StepExecutor)
        assert isinstance(executor, ProcessPoolStepExecutor)
        expect(len(set(executor._pids)) == STEP_PROCESSES)
        expect(
            (executor._run_limiter.total_tokens, executor._preview_limiter.total_tokens)
            == (STEP_PROCESSES - PREVIEW_STEP_PROCESSES, PREVIEW_STEP_PROCESSES)
        )
        assert_expectations()

    async def test_closing_the_container_stops_the_processes(self, fx_container: AsyncContainer) -> None:
        """Verify the processes the provider started are gone once the container is closed.

        :param fx_container: Container of the test.
        :type fx_container: AsyncContainer
        """
        with anyio.fail_after(TIMEOUT_S):
            executor = await fx_container.get(StepExecutor)
            assert isinstance(executor, ProcessPoolStepExecutor)
            pids = executor._pids
            await fx_container.close()
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
