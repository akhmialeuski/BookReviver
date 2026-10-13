"""The step executor that runs a processor in a pool of worker processes, so its work never holds the application's GIL.

OpenCV, Pillow and the Python between their calls keep the interpreter of the API process busy, which delays its event
loop, and with it every HTTP answer and every server-sent event. A step therefore runs in another process through
``anyio.to_process``, while the job coroutine, the write blocks, the events and the file copies stay in the API process.

The processor itself is not pickled. The catalogue of the API process holds configured processors, and one of them,
the dewarp, has loaded a neural model into memory. Each pool process builds its own catalogue once, from a source that
is a small value, and a call carries only the key of the processor, the scale and the ``StepInput`` that holds paths and
parameters. The source is the key of a cache, so it must be hashable, and it is pickled for every call, so it must be
picklable; a frozen ``attrs`` class of plain values is both.

``anyio`` keeps the processes it started and hands a call to the idle one used last, so the catalogue of a process is
built on its first call and reused after it. When a call takes an idle process, the other processes idle for 300
seconds or more are stopped, while the one it takes runs however long it was idle, so a pause alone costs no new start.

The count of processes at work is bounded by capacity limiters, one for the full runs and one for the previews, so a
preview waits behind no full run of another book.

A pool process imports the main module of the application again, as ``multiprocessing`` does with the spawn method, so
that module must run the application only under ``if __name__ == '__main__'``. The FastAPI command line does.
"""

import functools
from typing import TYPE_CHECKING, assert_never, override

import anyio

from bookreviver.domain.enums import VersionScale
from bookreviver.ports.processing import StepExecutor

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.ports.processing import ProcessorCatalog, StepInput, StepResult


@functools.cache
def _catalogue(source: Callable[[], ProcessorCatalog]) -> ProcessorCatalog:
    """Build the catalogue of the pool process the call runs in, once for each source.

    :param source: Builds the catalogue; hashable, since it is the key of the cache.
    :type source: Callable[[], ProcessorCatalog]
    :returns: The catalogue of this process.
    :rtype: ProcessorCatalog
    """
    return source()


def _run_step(
    source: Callable[[], ProcessorCatalog], processor_key: str, scale: VersionScale, step_input: StepInput
) -> StepResult:
    """Run the processor on the input; the function a pool process executes.

    :param source: Builds the catalogue of the pool process.
    :type source: Callable[[], ProcessorCatalog]
    :param processor_key: Key of the processor.
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
    processor = _catalogue(source).get(processor_key)
    work = processor.run if scale is VersionScale.FULL else processor.preview
    return work(step_input)


class ProcessPoolStepExecutor(StepExecutor):
    """Runs the processors of a catalogue in worker processes, a limited number for full runs and for previews."""

    def __init__(self, *, source: Callable[[], ProcessorCatalog], run_slots: int, preview_slots: int) -> None:
        """Run steps in at most ``run_slots`` processes for full runs and ``preview_slots`` for previews.

        :param source: Builds the catalogue in each pool process, which calls it once. It must be picklable and
                       hashable, such as a frozen class of plain values with a ``__call__``.
        :type source: Callable[[], ProcessorCatalog]
        :param run_slots: Most processes that run full runs at the same time.
        :type run_slots: int
        :param preview_slots: Most processes that run previews at the same time, apart from the full runs.
        :type preview_slots: int
        """
        self._source = source
        self._run_limiter = anyio.CapacityLimiter(run_slots)
        self._preview_limiter = anyio.CapacityLimiter(preview_slots)

    @override
    async def run(self, *, processor_key: str, scale: VersionScale, step_input: StepInput) -> StepResult:
        """Run the processor in a pool process, waiting for a free slot of the scale first.

        :param processor_key: Key of the processor, such as ``geometry.deskew``.
        :type processor_key: str
        :param scale: Whether the step runs on the full image or on the preview.
        :type scale: VersionScale
        :param step_input: What the step reads, with the directory it writes into.
        :type step_input: StepInput
        :returns: The outputs of the step.
        :rtype: StepResult
        :raises NotFoundError: If the catalogue has no processor with this key.
        :raises DomainError: If the processor cannot run on this input, with the type and the message it raised.
        """
        match scale:
            case VersionScale.FULL:
                limiter = self._run_limiter
            case VersionScale.PREVIEW:
                limiter = self._preview_limiter
            case _:
                assert_never(scale)
        return await anyio.to_process.run_sync(
            _run_step, self._source, processor_key, scale, step_input, limiter=limiter
        )
