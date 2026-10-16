"""The runners of the background jobs: what the job board calls to run a job, one entry for each ``JobKind``.

``JOB_ENTRIES`` is the registry of the entries. An entry takes the container of a request scope, the job and its
control, takes the service that does the work from the scope, and calls its one method, which does the tasks of the job
and returns how it ended. ``job_runner`` is the runner the board is built with: it opens a request scope for each job,
so a job gets its own unit of work like an HTTP request, picks the entry of the kind of the job, and runs it inside a
``job`` span.

A job kind without an entry cannot be run, and the runner refuses it at once, which the board records as a failed job.

The span is the root of a trace of its own: a job is a unit of work of its own, and the task that runs it copied the
context of whoever put the job on the board, so the span of a request would become its parent there. With no parent a
trace has the same shape however the job was put.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from bookreviver.domain.enums import JobKind, SpanAttribute, SpanName, SpanParent
from bookreviver.ports.tracing import Tracer
from bookreviver.services.imports import ImportService
from bookreviver.services.pages import PageService
from bookreviver.services.processing_jobs import ProcessingJobs

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from dishka import AsyncContainer

    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.domain.entities import Job
    from bookreviver.domain.values import JobEnd
    from bookreviver.ports.runtime import JobControl

# An entry: the container of the request scope the job runs in, the job and its control
type JobEntry = Callable[[AsyncContainer, Job, JobControl], Awaitable[JobEnd]]


async def import_source(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Run an import job: the entry of ``JobKind.IMPORT_SOURCE``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ImportService)).run_import(job, control)


async def prepare_pages(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Run a job that writes the images of pending page versions: the entry of ``JobKind.PREPARE_PAGES``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(PageService)).prepare_images(job, control)


async def run_stage(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Run a stage over its pages: the entry of ``JobKind.RUN_STAGE``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ProcessingJobs)).run_stage(job, control)


async def preview_step(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Preview the steps of a form on a page: the entry of ``JobKind.PREVIEW_STEP``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ProcessingJobs)).preview_step(job, control)


async def cut_tiles(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Cut the tile pyramids of some page versions: the entry of ``JobKind.CUT_TILES``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ProcessingJobs)).cut_tiles(job, control)


async def collect_versions(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Delete the old page versions nothing needs: the entry of ``JobKind.COLLECT_VERSIONS``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ProcessingJobs)).collect_versions(job, control)


async def measure_book(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Measure the book and write the medians into its normalize step: the entry of ``JobKind.MEASURE_BOOK``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ProcessingJobs)).measure_book(job, control)


async def detect_content(request: AsyncContainer, job: Job, control: JobControl) -> JobEnd:
    """Detect what the pages show and write it into them: the entry of ``JobKind.DETECT_CONTENT``.

    :param request: Container of the request scope of the job.
    :type request: AsyncContainer
    :param job: The running job.
    :type job: Job
    :param control: What the job reports through.
    :type control: JobControl
    :returns: How the job ended.
    :rtype: JobEnd
    """
    return await (await request.get(ProcessingJobs)).detect_content(job, control)


JOB_ENTRIES: Mapping[JobKind, JobEntry] = MappingProxyType(
    {
        JobKind.IMPORT_SOURCE: import_source,
        JobKind.PREPARE_PAGES: prepare_pages,
        JobKind.RUN_STAGE: run_stage,
        JobKind.PREVIEW_STEP: preview_step,
        JobKind.CUT_TILES: cut_tiles,
        JobKind.COLLECT_VERSIONS: collect_versions,
        JobKind.MEASURE_BOOK: measure_book,
        JobKind.DETECT_CONTENT: detect_content,
    }
)


def job_runner(container: AsyncContainer, *, entries: Mapping[JobKind, JobEntry] = JOB_ENTRIES) -> JobRunner:
    """Build the runner the job board is made with, which runs each job in a request scope inside a ``job`` span.

    The span is current while the job runs, so the spans of the work the job does become its children. The tracer comes
    from the application container, which a request scope is a child of.

    :param container: Application container, which provides the tracer and opens a request scope for every job.
    :type container: AsyncContainer
    :param entries: The entry of every job kind.
    :type entries: Mapping[JobKind, JobEntry]
    :returns: The runner, which raises ``KeyError`` for a kind with no entry.
    :rtype: JobRunner
    """

    async def run(job: Job, control: JobControl) -> JobEnd:
        """Run the job through the entry of its kind, in a request scope and a span of its own.

        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which the entry lets through.
        :raises KeyError: If no entry is registered for the kind of the job.
        """
        tracer = await container.get(Tracer)
        attributes = {SpanAttribute.JOB_KIND: job.kind, SpanAttribute.JOB_ID: str(job.id)}
        with tracer.span(SpanName.JOB, attributes, parent=SpanParent.NONE):
            async with container() as request:
                return await entries[job.kind](request, job, control)

    return run
