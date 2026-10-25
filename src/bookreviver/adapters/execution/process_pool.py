"""The step executor that runs a processor in a pool of worker processes, so its work never holds the application's GIL.

OpenCV, Pillow and the Python between their calls keep the interpreter of the API process busy, which delays its event
loop, and with it every HTTP answer and every server-sent event. A step therefore runs in another process of a
``concurrent.futures.ProcessPoolExecutor``, while the job coroutine, the write blocks, the events and the file copies
stay in the API process. A call submits the step and waits for its future in a worker thread, so the event loop stays
free, and a step that has started cannot be cancelled: the caller is released when the process answers. Those threads
come from a limiter of the executor sized to its processes, never from the default threads of anyio, which would
otherwise sit idle in the wait while the file copies of the application queued behind them.

The processor itself is not pickled. The catalogue of the API process holds configured processors, and one of them,
the dewarp, has loaded a neural model into memory. Each pool process builds its own catalogue once, from a source that
is a small value, and a call carries only the key of the processor, the scale and the ``StepInput`` that holds paths and
parameters. The source travels to each process once, with the initializer, so it must be picklable; a frozen ``attrs``
class of plain values is.

The initializer runs once in each process, before its first call. It first sets the thread counts of the numeric
libraries by environment variables and only then builds the catalogue, because building it imports the plugins and with
them OpenCV, which reads its variable when it starts. Without a limit every process starts as many threads as the
machine has cores, and the processes fight over them; each gets its share of the cores instead. Setting variables
keeps this module free of any image library.

Importing the plugins and building the catalogue takes seconds, so the executor starts every process when it is entered
and waits until all of them have run the initializer, instead of letting the first step of each process pay for it.
The warm-up submits one call for each process, and the calls wait for each other at a barrier, so each process takes
exactly one of them and the pool is known to hold that many live processes before the first step.

The count of processes at work is bounded by capacity limiters, one for the full runs and one for the previews, so a
preview waits behind no full run of another book.

A ``ProcessPoolExecutor`` whose process dies, killed by the system for its memory or crashed in native code, stops for
good: every running and every later call fails with ``BrokenProcessPool``, and the standard library offers no way to
mend it. The executor therefore starts a new pool in its place and lets the broken one go, while the steps that were in
the broken pool fail with that error, so a job reports them as unexpected and the next step runs in the new pool. The
same happens when a result cannot be unpickled in the API process, so an error a processor raises other than a domain
error crosses the boundary as a ``RuntimeError`` that names its class and message, and never breaks the pool.

A pool process imports the main module of the application again, as ``multiprocessing`` does with the forkserver and
spawn methods, so that module must run the application only under ``if __name__ == '__main__'``. The FastAPI command
line does.
"""

import enum
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Self, assert_never, override

import anyio

from bookreviver.domain.enums import VersionScale
from bookreviver.domain.errors import DomainError
from bookreviver.ports.processing import StepExecutor

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.synchronize import Barrier
    from types import TracebackType

    from bookreviver.ports.processing import ProcessorCatalog, StepInput, StepResult

# Seconds the processes of the pool have to run their initializers and meet at the barrier of the warm-up
WARM_UP_TIMEOUT_S: float = 120.0
NOT_STARTED_MESSAGE: str = 'The process pool is not started: enter the executor before running a step.'
NOT_INITIALIZED_MESSAGE: str = 'The pool process ran a call before its initializer.'
FOREIGN_ERROR_MESSAGE: str = 'The processor {key} failed with {kind}: {message}'


class ThreadVariable(enum.StrEnum):
    """An environment variable that limits the threads of a numeric library, read when the library starts."""

    OPENCV = 'OPENCV_FOR_THREADS_NUM'
    OPENMP = 'OMP_NUM_THREADS'
    OPENBLAS = 'OPENBLAS_NUM_THREADS'
    MKL = 'MKL_NUM_THREADS'


def threads_per_process(*, processes: int) -> int:
    """Share the cores this process may use between the pool processes.

    :param processes: Processes of the pool, which work at the same time.
    :type processes: int
    :returns: Threads each process may start, and at least one.
    :rtype: int
    """
    return max(1, (os.process_cpu_count() or 1) // processes)


class _Worker:
    """What a pool process keeps between its calls, set by the initializer.

    :cvar processors: The processors of this process, or None before the initializer ran.
    :cvar barrier: Where the calls of the warm-up wait for each other, or None before the initializer ran.
    """

    processors: ClassVar[ProcessorCatalog | None] = None
    barrier: ClassVar[Barrier | None] = None


def _initialize(source: Callable[[], ProcessorCatalog], threads: int, barrier: Barrier) -> None:
    """Limit the threads of the numeric libraries, then build the catalogue; runs once in each pool process.

    :param source: Builds the catalogue of the pool process.
    :type source: Callable[[], ProcessorCatalog]
    :param threads: Threads each numeric library of this process may start.
    :type threads: int
    :param barrier: Where the calls of the warm-up wait for each other.
    :type barrier: Barrier
    """
    for variable in ThreadVariable:
        os.environ[variable] = str(threads)
    _Worker.processors = source()
    _Worker.barrier = barrier


def _ready() -> None:
    """Wait until every process of the pool has taken a call of the warm-up; the function a pool process executes.

    :raises RuntimeError: If the initializer has not run in this process.
    :raises BrokenBarrierError: If the other processes did not come within ``WARM_UP_TIMEOUT_S`` seconds.
    """
    if _Worker.barrier is None:
        raise RuntimeError(NOT_INITIALIZED_MESSAGE)
    _Worker.barrier.wait(WARM_UP_TIMEOUT_S)


def _run_step(processor_key: str, scale: VersionScale, step_input: StepInput) -> StepResult:
    """Run the processor on the input; the function a pool process executes.

    :param processor_key: Key of the processor.
    :type processor_key: str
    :param scale: Whether the step runs on the full image or on the preview.
    :type scale: VersionScale
    :param step_input: What the step reads, with the directory it writes into.
    :type step_input: StepInput
    :returns: The outputs of the step.
    :rtype: StepResult
    :raises RuntimeError: If the initializer has not run in this process, or the processor raised an error other than a
                          domain error, whose class and message it carries.
    :raises NotFoundError: If the catalogue has no processor with this key.
    :raises DomainError: If the processor cannot run on this input.
    """
    if _Worker.processors is None:
        raise RuntimeError(NOT_INITIALIZED_MESSAGE)
    processor = _Worker.processors.get(processor_key)
    work = processor.run if scale is VersionScale.FULL else processor.preview
    try:
        return work(step_input)
    except DomainError:
        raise
    # A library error may not unpickle in the API process, which would break the whole pool, so only its text crosses
    except Exception as error:
        err_msg = FOREIGN_ERROR_MESSAGE.format(key=processor_key, kind=type(error).__qualname__, message=error)
        raise RuntimeError(err_msg) from error


class ProcessPoolStepExecutor(StepExecutor):
    """Runs the processors of a catalogue in worker processes, a limited number for full runs and for previews.

    The executor owns the processes, so it is entered before its first step and left to stop them: ``async with``.
    """

    def __init__(self, *, source: Callable[[], ProcessorCatalog], run_slots: int, preview_slots: int) -> None:
        """Run steps in at most ``run_slots`` processes for full runs and ``preview_slots`` for previews.

        :param source: Builds the catalogue in each pool process, which calls it once. It must be picklable, such as a
                       frozen class of plain values with a ``__call__``.
        :type source: Callable[[], ProcessorCatalog]
        :param run_slots: Most processes that run full runs at the same time.
        :type run_slots: int
        :param preview_slots: Most processes that run previews at the same time, apart from the full runs.
        :type preview_slots: int
        """
        self._source = source
        self._processes = run_slots + preview_slots
        self._run_limiter = anyio.CapacityLimiter(run_slots)
        self._preview_limiter = anyio.CapacityLimiter(preview_slots)
        # The threads that wait for the processes come from here, so a waiting step takes none of the default threads
        # of anyio, which the file copies and the other blocking calls of the application need
        self._waiters = anyio.CapacityLimiter(self._processes)
        self._pool: ProcessPoolExecutor | None = None
        # Held while the pool is replaced or stopped, so a step never meets an executor without a pool in between
        self._swap = anyio.Lock()

    async def __aenter__(self) -> Self:
        """Start every process of the pool and wait until each has built its catalogue.

        :returns: This executor, ready to run steps.
        :rtype: Self
        :raises BrokenProcessPool: If a process died while it started.
        :raises BrokenBarrierError: If the processes did not all start within ``WARM_UP_TIMEOUT_S`` seconds.
        """
        self._pool = await self._start_pool()
        return self

    async def _start_pool(self) -> ProcessPoolExecutor:
        """Start a pool of every process and wait until each has built its catalogue.

        :returns: The started pool, whose processes have all run the initializer.
        :rtype: ProcessPoolExecutor
        :raises BrokenProcessPool: If a process died while it started.
        :raises BrokenBarrierError: If the processes did not all start within ``WARM_UP_TIMEOUT_S`` seconds.
        """
        barrier = multiprocessing.Barrier(self._processes)
        pool = ProcessPoolExecutor(
            max_workers=self._processes,
            initializer=_initialize,
            initargs=(self._source, threads_per_process(processes=self._processes), barrier),
        )
        try:
            futures = [pool.submit(_ready) for _ in range(self._processes)]
            for future in futures:
                await anyio.to_thread.run_sync(future.result, limiter=self._waiters)
        except BaseException:
            await self._shut_down(pool)
            raise
        return pool

    @staticmethod
    async def _shut_down(pool: ProcessPoolExecutor) -> None:
        """Cancel the calls of the pool that wait for a process, wait for the others, and let the processes exit.

        :param pool: The pool to stop, which may be broken already.
        :type pool: ProcessPoolExecutor
        """
        await anyio.to_thread.run_sync(partial(pool.shutdown, wait=True, cancel_futures=True))

    async def _replace(self, broken: ProcessPoolExecutor) -> None:
        """Put a new pool in the place of a broken one, unless another step or the exit has done it already.

        The steps that met the same broken pool all call this, and only the first starts a new pool.

        :param broken: The pool a step found broken.
        :type broken: ProcessPoolExecutor
        :raises BrokenProcessPool: If a process of the new pool died while it started.
        :raises BrokenBarrierError: If the processes of the new pool did not all start within ``WARM_UP_TIMEOUT_S``.
        """
        async with self._swap:
            if self._pool is broken:
                self._pool = await self._start_pool()
        await self._shut_down(broken)

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Stop the processes, after the steps that have started.

        :param exc_type: Type of the exception that ends the block, or None.
        :type exc_type: type[BaseException] | None
        :param exc: The exception that ends the block, or None.
        :type exc: BaseException | None
        :param traceback: Traceback of that exception, or None.
        :type traceback: TracebackType | None
        """
        await self._stop()

    async def _stop(self) -> None:
        """Stop the current pool, after a replacement that has begun, so the pool it starts is stopped too."""
        async with self._swap:
            pool, self._pool = self._pool, None
        if pool is not None:
            await self._shut_down(pool)

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
        :raises RuntimeError: If the executor is not entered, or was left, or the processor raised an error other than a
                              domain error, whose class and message it carries.
        :raises NotFoundError: If the catalogue has no processor with this key.
        :raises DomainError: If the processor cannot run on this input, with the type and the message it raised.
        :raises BrokenProcessPool: If a process of the pool died during the step; a new pool runs the next steps.
        """
        match scale:
            case VersionScale.FULL:
                limiter = self._run_limiter
            case VersionScale.PREVIEW:
                limiter = self._preview_limiter
            case _:
                assert_never(scale)
        async with limiter:
            if (pool := self._pool) is None:
                raise RuntimeError(NOT_STARTED_MESSAGE)
            try:
                future = pool.submit(_run_step, processor_key, scale, step_input)
                return await anyio.to_thread.run_sync(future.result, limiter=self._waiters)
            except BrokenProcessPool:
                await self._replace(pool)
                raise
