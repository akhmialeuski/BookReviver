"""Every StepExecutor adapter with a catalogue of processors that tell which method of which process ran them.

The processors need no image library and are module-level classes, so a pool process imports them by the path of this
module, as it must for the source of its catalogue. ``ProbeProcessor`` reports the method that ran and the process it
ran in, ``FailingProcessor`` raises a domain error with a plain message, and ``WaitingProcessor`` holds its run until a
file exists, which lets a test keep a slot of the pool busy as long as it needs.
"""

import enum
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, override

from attrs import frozen

from bookreviver.adapters.execution.in_process import InProcessStepExecutor
from bookreviver.adapters.execution.process_pool import ProcessPoolStepExecutor
from bookreviver.domain.enums import ColorMode, Stage
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.values import ProcessorSpec
from bookreviver.ports.processing import Processor, StepInput, StepOutput, StepResult
from tests.helpers.fake_processing import FakeCatalogue

if TYPE_CHECKING:
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import StepExecutor

# The keys of the processors of the catalogue
PROBE_KEY: str = 'geometry.probe'
FAILING_KEY: str = 'geometry.failing'
WAITING_KEY: str = 'geometry.waiting'

# What a probe writes into the data of its output
METHOD_KEY: str = 'method'
PID_KEY: str = 'pid'
RUN_METHOD: str = 'run'
PREVIEW_METHOD: str = 'preview'

# The message the failing processor raises with
FAILING_MESSAGE: str = 'The probe was told to fail.'

# The parameter of the waiting processor naming the file its run waits for
FLAG_PARAMETER: str = 'flag'
WAIT_LIMIT_S: float = 20.0
WAIT_POLL_S: float = 0.01
WAIT_TIMEOUT_MESSAGE: str = 'The flag file did not appear before the wait ran out.'


class StepExecutorAdapter(enum.StrEnum):
    """An adapter of the StepExecutor port under test."""

    IN_PROCESS = 'in-process'
    PROCESS_POOL = 'process-pool'


class ProbeProcessor(Processor):
    """A processor that writes the method that ran and the identifier of its process into the data of its output."""

    spec = ProcessorSpec(key=PROBE_KEY, version='1', title='Probe', stage=Stage.GEOMETRY)

    @override
    def validate_params(self, raw: MetadataMap) -> MetadataMap:
        """Accept the parameters as they are.

        :param raw: Parameters to check.
        :type raw: MetadataMap
        :returns: The same parameters.
        :rtype: MetadataMap
        """
        return raw

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Report a full run.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output saying that ``run`` ran, and in which process.
        :rtype: StepResult
        """
        return self._reported(RUN_METHOD)

    @override
    def preview(self, step_input: StepInput) -> StepResult:
        """Report a preview.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output saying that ``preview`` ran, and in which process.
        :rtype: StepResult
        """
        return self._reported(PREVIEW_METHOD)

    @staticmethod
    def _reported(method: str) -> StepResult:
        """Make the result that names a method and the process it ran in.

        :param method: Name of the method that ran.
        :type method: str
        :returns: One output holding the method and the process identifier in its data.
        :rtype: StepResult
        """
        data = {METHOD_KEY: method, PID_KEY: os.getpid()}
        return StepResult(outputs=[StepOutput(color_mode=ColorMode.GRAY, data=data)])


class FailingProcessor(ProbeProcessor):
    """A processor whose run fails with a domain error that carries a plain message."""

    spec = ProcessorSpec(key=FAILING_KEY, version='1', title='Failing', stage=Stage.GEOMETRY)

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Fail the run.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: Nothing, since it always fails.
        :rtype: StepResult
        :raises InvalidParametersError: Always, with ``FAILING_MESSAGE``.
        """
        raise InvalidParametersError(FAILING_MESSAGE)


class WaitingProcessor(ProbeProcessor):
    """A processor whose run waits until the file named by its parameter ``flag`` exists, and then reports itself."""

    spec = ProcessorSpec(key=WAITING_KEY, version='1', title='Waiting', stage=Stage.GEOMETRY)

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Wait for the flag file by polling for it, and then report a full run.

        :param step_input: What the step reads, whose ``flag`` parameter is the path of the file to wait for.
        :type step_input: StepInput
        :returns: One output saying that ``run`` ran, and in which process.
        :rtype: StepResult
        :raises ConflictError: If the flag file does not appear within ``WAIT_LIMIT_S`` seconds.
        """
        flag = Path(step_input.params[FLAG_PARAMETER])
        deadline = time.monotonic() + WAIT_LIMIT_S
        while not flag.exists():
            if time.monotonic() > deadline:
                raise ConflictError(WAIT_TIMEOUT_MESSAGE)
            time.sleep(WAIT_POLL_S)
        return self._reported(RUN_METHOD)


@frozen
class ProbeCatalogueSource:
    """Builds the catalogue of the three processors above; a value, so it is hashable and travels to a pool process."""

    def __call__(self) -> FakeCatalogue:
        """Build the catalogue.

        :returns: A catalogue of a probe, a failing processor and a waiting one.
        :rtype: FakeCatalogue
        """
        return FakeCatalogue([ProbeProcessor(), FailingProcessor(), WaitingProcessor()])


def make_step_input(workdir: Path, **params: object) -> StepInput:
    """Build the input of a step that reads no image.

    :param workdir: Directory the step writes into.
    :type workdir: Path
    :param params: Parameters of the step.
    :type params: object
    :returns: The input.
    :rtype: StepInput
    """
    return StepInput(image=None, params=params, workdir=workdir)


def build_executor_under_test(
    adapter: StepExecutorAdapter, *, run_slots: int = 1, preview_slots: int = 1
) -> StepExecutor:
    """Build an adapter over the catalogue of the probes.

    :param adapter: Adapter to build.
    :type adapter: StepExecutorAdapter
    :param run_slots: Processes for full runs, which the pool adapter reads and the thread adapter ignores.
    :type run_slots: int
    :param preview_slots: Processes for previews, which the pool adapter reads and the thread adapter ignores.
    :type preview_slots: int
    :returns: The executor. A pool stops its processes when the event loop of the test ends.
    :rtype: StepExecutor
    """
    source = ProbeCatalogueSource()
    match adapter:
        case StepExecutorAdapter.IN_PROCESS:
            return InProcessStepExecutor(source())
        case StepExecutorAdapter.PROCESS_POOL:
            return ProcessPoolStepExecutor(source=source, run_slots=run_slots, preview_slots=preview_slots)
