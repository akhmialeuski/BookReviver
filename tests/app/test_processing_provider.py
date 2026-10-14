"""Tests for the processing provider: the executor of the steps the container builds."""

import multiprocessing
from typing import TYPE_CHECKING
from unittest.mock import ANY, patch

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.app.container import build_container
from bookreviver.ports.processing import StepExecutor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from unittest.mock import MagicMock

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

EXECUTOR_PATCH: str = 'bookreviver.app.providers.processing.ProcessPoolStepExecutor'
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

    # No autospec: mock evaluates the signature of the constructor, whose annotations name types imported for checking only
    @patch(EXECUTOR_PATCH)
    async def test_previews_get_their_own_processes_and_full_runs_get_the_rest(
        self, mock_executor: MagicMock, fx_container: AsyncContainer
    ) -> None:
        """Verify the executor is built with the preview processes of the settings and the rest for full runs.

        :param mock_executor: The executor class, replaced so that no process starts.
        :type mock_executor: MagicMock
        :param fx_container: Container of the test.
        :type fx_container: AsyncContainer
        """
        await fx_container.get(StepExecutor)
        mock_executor.assert_called_once_with(
            source=ANY,
            run_slots=STEP_PROCESSES - PREVIEW_STEP_PROCESSES,
            preview_slots=PREVIEW_STEP_PROCESSES,
        )

    async def test_the_container_starts_the_processes_and_closing_it_stops_them(
        self, fx_container: AsyncContainer
    ) -> None:
        """Verify building the executor starts the processes of the settings, and closing the container ends them all.

        :param fx_container: Container of the test.
        :type fx_container: AsyncContainer
        """
        before = set(multiprocessing.active_children())
        with anyio.fail_after(TIMEOUT_S):
            await fx_container.get(StepExecutor)
            started = set(multiprocessing.active_children()) - before
            await fx_container.close()
        expect(len(started) == STEP_PROCESSES)
        expect(not any(process.is_alive() for process in started))
        assert_expectations()
