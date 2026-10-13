"""Tests for the processing provider: the executor of the steps the container builds."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.execution.process_pool import ProcessPoolStepExecutor
from bookreviver.app.container import build_container
from bookreviver.ports.processing import StepExecutor

if TYPE_CHECKING:
    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

STEP_PROCESSES: int = 6
PREVIEW_STEP_PROCESSES: int = 2


class TestStepExecutorProvider:
    """Tests for ProcessingProvider.step_executor()."""

    async def test_previews_get_their_own_processes_and_full_runs_get_the_rest(self, fx_settings: Settings) -> None:
        """Verify the pool is built with the processes of the settings, split between full runs and previews.

        :param fx_settings: Settings of the suite.
        :type fx_settings: Settings
        """
        processing = fx_settings.processing.model_copy(
            update={'step_processes': STEP_PROCESSES, 'preview_step_processes': PREVIEW_STEP_PROCESSES}
        )
        container = build_container(fx_settings.model_copy(update={'processing': processing}))
        executor = await container.get(StepExecutor)
        await container.close()
        assert isinstance(executor, ProcessPoolStepExecutor)
        assert (executor._run_limiter.total_tokens, executor._preview_limiter.total_tokens) == (
            STEP_PROCESSES - PREVIEW_STEP_PROCESSES,
            PREVIEW_STEP_PROCESSES,
        )
