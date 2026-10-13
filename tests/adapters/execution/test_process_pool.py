"""Tests for what only the process pool executor does, beyond the StepExecutor contract in ``tests/contracts``.

The processors and the executor come from ``tests.helpers.step_executors``, which the contract suite uses too.
"""

import os
import re
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.execution.process_pool import (
    NOT_STARTED_MESSAGE,
    ProcessPoolStepExecutor,
    ThreadVariable,
    threads_per_process,
)
from bookreviver.app.plugins import EntryPointCatalogSource
from bookreviver.domain.enums import VersionData, VersionScale, WorkerPool
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import CV_MISSING, save, text_page
from tests.helpers.step_executors import (
    METHOD_KEY,
    PID_KEY,
    PREVIEW_METHOD,
    PROBE_KEY,
    THREADS_KEY,
    WAITING_KEY,
    ProbeCatalogueSource,
    StepExecutorAdapter,
    make_step_input,
    open_executor_under_test,
)

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.ports.processing import StepResult

pytestmark = pytest.mark.anyio

# Seconds a test lets the pool work before it fails, which bounds a run that never ends
TIMEOUT_S: float = 30.0
DESKEW_KEY: str = 'geometry.deskew'
# The slots of the pools the tests start: processes enough to tell them apart, and few enough to start quickly
RUN_SLOTS: int = 2
PREVIEW_SLOTS: int = 1
PAGE_SIZE_PX: tuple[int, int] = (900, 1_200)


class TestProcessPoolStepExecutor:
    """Tests for ProcessPoolStepExecutor."""

    async def test_the_step_runs_in_another_process(self, tmp_path: Path) -> None:
        """Verify the processor reports an identifier of a process that is not the one of the test.

        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        with anyio.fail_after(TIMEOUT_S):
            async with open_executor_under_test(StepExecutorAdapter.PROCESS_POOL) as executor:
                result = await executor.run(
                    processor_key=PROBE_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path)
                )
        [output] = result.outputs
        assert output.data[PID_KEY] != os.getpid()

    async def test_every_process_is_started_before_the_first_step(self) -> None:
        """Verify entering the executor leaves one live process for each slot, none of them the test's own.

        The processes are the ones the warm-up calls ran in, which wait for each other, so each took exactly one.
        """
        slots = RUN_SLOTS + PREVIEW_SLOTS
        with anyio.fail_after(TIMEOUT_S):
            async with ProcessPoolStepExecutor(
                source=ProbeCatalogueSource(), run_slots=RUN_SLOTS, preview_slots=PREVIEW_SLOTS
            ) as executor:
                pids = executor._pids
                for pid in pids:
                    os.kill(pid, 0)
        expect(len(set(pids)) == slots)
        expect(os.getpid() not in pids)
        assert_expectations()

    async def test_leaving_the_executor_stops_every_process(self) -> None:
        """Verify no process of the pool is left after the block ends, and a step is refused after it."""
        with anyio.fail_after(TIMEOUT_S):
            async with ProcessPoolStepExecutor(
                source=ProbeCatalogueSource(), run_slots=RUN_SLOTS, preview_slots=PREVIEW_SLOTS
            ) as executor:
                pids = executor._pids
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)

    async def test_a_step_before_the_executor_is_entered_is_refused(self, tmp_path: Path) -> None:
        """Verify a step that arrives before the processes are started fails with a clear error.

        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        executor = ProcessPoolStepExecutor(
            source=ProbeCatalogueSource(), run_slots=RUN_SLOTS, preview_slots=PREVIEW_SLOTS
        )
        with pytest.raises(RuntimeError, match=re.escape(NOT_STARTED_MESSAGE)):
            await executor.run(processor_key=PROBE_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path))

    async def test_a_step_after_the_executor_is_left_is_refused(self, tmp_path: Path) -> None:
        """Verify a step that arrives after the processes are stopped fails with the same error.

        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        with anyio.fail_after(TIMEOUT_S):
            async with ProcessPoolStepExecutor(
                source=ProbeCatalogueSource(), run_slots=RUN_SLOTS, preview_slots=PREVIEW_SLOTS
            ) as executor:
                pass
        with pytest.raises(RuntimeError, match=re.escape(NOT_STARTED_MESSAGE)):
            await executor.run(processor_key=PROBE_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path))

    async def test_each_process_limits_the_threads_of_its_libraries_to_its_share_of_the_cores(
        self, tmp_path: Path
    ) -> None:
        """Verify a processor sees every thread limit set to the share of the cores that its process may use.

        :param tmp_path: Temporary directory of the test, the work directory of the step.
        :type tmp_path: Path
        """
        share = str(threads_per_process(processes=RUN_SLOTS + PREVIEW_SLOTS))
        with anyio.fail_after(TIMEOUT_S):
            async with ProcessPoolStepExecutor(
                source=ProbeCatalogueSource(), run_slots=RUN_SLOTS, preview_slots=PREVIEW_SLOTS
            ) as executor:
                result = await executor.run(
                    processor_key=THREADS_KEY, scale=VersionScale.FULL, step_input=make_step_input(tmp_path)
                )
        [output] = result.outputs
        assert output.data == {variable.value: share for variable in ThreadVariable}

    async def test_a_preview_finishes_while_a_full_run_holds_the_only_run_slot(self, tmp_path: Path) -> None:
        """Verify a preview does not wait for the full run that keeps every slot of the full runs busy.

        The full run takes the slot before its task first blocks, and then waits for a flag file, so the preview runs
        while the slot is taken and the full run ends only when the test lets it.

        :param tmp_path: Temporary directory of the test, the work directory of the steps.
        :type tmp_path: Path
        """
        flag = tmp_path / 'flag'
        full_input = make_step_input(tmp_path, flag=str(flag))
        full_results: list[StepResult] = []

        with anyio.fail_after(TIMEOUT_S):
            async with (
                open_executor_under_test(StepExecutorAdapter.PROCESS_POOL, run_slots=1, preview_slots=1) as executor,
                anyio.create_task_group() as tasks,
            ):

                async def run_full() -> None:
                    """Run the processor that waits for the flag and keep its result."""
                    full_results.append(
                        await executor.run(processor_key=WAITING_KEY, scale=VersionScale.FULL, step_input=full_input)
                    )

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
        with anyio.fail_after(TIMEOUT_S):
            async with ProcessPoolStepExecutor(source=source, run_slots=1, preview_slots=1) as executor:
                result = await executor.run(processor_key=DESKEW_KEY, scale=VersionScale.FULL, step_input=step_input)
        [output] = result.outputs
        assert VersionData.ANGLE in output.data


class TestThreadsPerProcess:
    """Tests for threads_per_process()."""

    @pytest.mark.parametrize(
        ('cores', 'processes', 'expected'),
        [(14, 8, 1), (14, 2, 7), (14, 3, 4), (1, 4, 1), (None, 2, 1)],
        ids=['more-cores-than-processes', 'even-share', 'rounded-down', 'one-core', 'unknown-cores'],
    )
    def test_the_cores_are_shared_between_the_processes_at_least_one_thread_each(
        self, monkeypatch: pytest.MonkeyPatch, cores: int | None, processes: int, expected: int
    ) -> None:
        """Verify each process gets the whole share of the cores rounded down, and at least one thread.

        :param monkeypatch: Fixture that replaces the count of cores for the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param cores: Cores the process may use, or None when the platform cannot tell.
        :type cores: int | None
        :param processes: Processes of the pool.
        :type processes: int
        :param expected: Threads each process may start.
        :type expected: int
        """
        monkeypatch.setattr(os, 'process_cpu_count', lambda: cores)
        assert threads_per_process(processes=processes) == expected
