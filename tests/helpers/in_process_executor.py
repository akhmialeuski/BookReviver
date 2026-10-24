"""The step executor that runs a processor in a worker thread of the application process.

It serves the tests of the services and the contract of the port. A processor that holds the interpreter for long
delays the event loop of the application, so the application itself runs its steps in a pool of processes.
"""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.domain.enums import VersionScale
from bookreviver.ports.processing import StepExecutor

if TYPE_CHECKING:
    from bookreviver.ports.processing import ProcessorCatalog, StepInput, StepResult


class InProcessStepExecutor(StepExecutor):
    """Runs the processors of a catalogue in a worker thread, so a step does not block the event loop by itself."""

    def __init__(self, processors: ProcessorCatalog) -> None:
        """Run the processors of the catalogue.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        """
        self._processors = processors

    @override
    async def run(self, *, processor_key: str, scale: VersionScale, step_input: StepInput) -> StepResult:
        """Run the processor in a worker thread.

        :param processor_key: Key of the processor, such as ``geometry.deskew``.
        :type processor_key: str
        :param scale: Whether the step runs on the full image or on the preview.
        :type scale: VersionScale
        :param step_input: What the step reads, with the directory it writes into.
        :type step_input: StepInput
        :returns: The outputs of the step.
        :rtype: StepResult
        :raises NotFoundError: If the catalogue has no processor with this key.
        :raises DomainError: If the processor cannot run on this input.
        """
        processor = self._processors.get(processor_key)
        work = processor.run if scale is VersionScale.FULL else processor.preview
        return await anyio.to_thread.run_sync(work, step_input)
