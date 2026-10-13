"""Tests for what only the process pool executor does, beyond the StepExecutor contract in ``tests/contracts``.

The processors and the executor come from ``tests.helpers.step_executors``, which the contract suite uses too.
"""

import os
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.execution.process_pool import ProcessPoolStepExecutor
from bookreviver.app.plugins import EntryPointCatalogSource
from bookreviver.domain.enums import VersionData, VersionScale, WorkerPool
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import CV_MISSING, save, text_page
from tests.helpers.step_executors import (
    METHOD_KEY,
    PID_KEY,
    PREVIEW_METHOD,
    PROBE_KEY,
    WAITING_KEY,
    StepExecutorAdapter,
    build_executor_under_test,
    make_step_input,
)

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.ports.processing import StepResult

pytestmark = pytest.mark.anyio

# Seconds a test lets the pool work before it fails, which bounds a run that never ends
TIMEOUT_S: float = 30.0
DESKEW_KEY: str = 'geometry.deskew'
PAGE_SIZE_PX: tuple[int, int] = (900, 1_200)


class TestProcessPoolStepExecutor:
    """Tests for ProcessPoolStepExecutor."""

    async def test_the_step_runs_in_another_process(self, tmp_path: Path) -> None:
        """Verify the processor reports an identifier of a process that is not the one of the test.

        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        executor = build_executor_under_test(StepExecutorAdapter.PROCESS_POOL)
        with anyio.fail_after(TIMEOUT_S):
            result = await executor.run(
                processor_key=PROBE_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path)
            )
        [output] = result.outputs
        assert output.data[PID_KEY] != os.getpid()

    async def test_a_preview_finishes_while_a_full_run_holds_the_only_run_slot(self, tmp_path: Path) -> None:
        """Verify a preview does not wait for the full run that keeps every slot of the full runs busy.

        The full run takes the slot before its task first blocks, and then waits for a flag file, so the preview runs
        while the slot is taken and the full run ends only when the test lets it.

        :param tmp_path: Temporary directory of the test, the work directory of the steps.
        :type tmp_path: Path
        """
        executor = build_executor_under_test(StepExecutorAdapter.PROCESS_POOL, run_slots=1, preview_slots=1)
        flag = tmp_path / 'flag'
        full_input = make_step_input(tmp_path, flag=str(flag))
        full_results: list[StepResult] = []

        async def run_full() -> None:
            """Run the processor that waits for the flag and keep its result."""
            full_results.append(
                await executor.run(processor_key=WAITING_KEY, scale=VersionScale.FULL, step_input=full_input)
            )

        with anyio.fail_after(TIMEOUT_S):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(run_full)
                await anyio.wait_all_tasks_blocked()
                preview = await executor.run(
                    processor_key=PROBE_KEY, scale=VersionScale.PREVIEW, step_input=make_step_input(tmp_path)
                )
                [preview_output] = preview.outputs
                expect(preview_output.data[METHOD_KEY] == PREVIEW_METHOD)
                expect(not full_results)
                flag.touch()
        expect(len(full_results) == 1)
        assert_expectations()

    async def test_a_step_of_the_built_in_catalogue_runs_in_the_pool(self, tmp_path: Path) -> None:
        """Verify a pool process builds the catalogue of the entry points and runs the deskew of OpenCV from it.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        pytest.importorskip('cv2', reason=CV_MISSING)
        source = EntryPointCatalogSource(pools=frozenset(WorkerPool))
        workdir = tmp_path / 'work'
        workdir.mkdir()
        step_input = StepInput(
            image=save(text_page(*PAGE_SIZE_PX), tmp_path / 'page.png'),
            params=source().get(DESKEW_KEY).validate_params({}),
            workdir=workdir,
        )
        executor = ProcessPoolStepExecutor(source=source, run_slots=1, preview_slots=1)
        with anyio.fail_after(TIMEOUT_S):
            result = await executor.run(processor_key=DESKEW_KEY, scale=VersionScale.FULL, step_input=step_input)
        [output] = result.outputs
        assert VersionData.ANGLE in output.data
