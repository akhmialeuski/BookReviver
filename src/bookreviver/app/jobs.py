"""The running of the background jobs: one runner for every job, and the worker each kind of job is done by.

A job is what was asked for, a step or a stage with its pages and its parameters, and a worker is the one method of a
service that does it. ``job_workers`` names the worker of each ``JobKind``, and every worker takes the same three
things: the book of the queue the job was taken from, the job and its control. ``job_runner`` is the runner the board
is built with, the same for every job: it opens a request scope, so a job gets its own catalogue database like an HTTP
request, opens the book of the queue from the name of its directory, asks the scope for the workers, and runs the one
of the kind of the job inside a ``job`` span. A job kind without a worker cannot be run, and the runner refuses it at
once, which the board records as a failed job.

The span is the root of a trace of its own: a job is work of its own, and the task that runs it copied the
context of whoever put the job on the board, so the span of a request would become its parent there. With no parent a
trace has the same shape however the job was put.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.core.book import Book
from bookreviver.domain.jobs import JobKind
from bookreviver.domain.spans import SpanAttribute, SpanName, SpanParent
from bookreviver.ports.tracing import Tracer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from dishka import AsyncContainer

    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.domain.ids import BookDirectoryName
    from bookreviver.domain.jobs import Job, JobEnd
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.imports import ImportService
    from bookreviver.services.pages import PageService
    from bookreviver.services.processing_jobs import ProcessingJobs

# A worker: the book of the queue the job was taken from, the job and its control, to how the job ended
type JobWorker = Callable[[Book, Job, JobControl], Awaitable[JobEnd]]


@frozen
class JobWorkers:
    """The worker of each kind of job, built for the request scope one job runs in.

    :ivar by_kind: The worker of each kind.
    """

    by_kind: Mapping[JobKind, JobWorker]

    def of(self, kind: JobKind) -> JobWorker:
        """Give the worker of a kind of job.

        :param kind: What the job does.
        :type kind: JobKind
        :returns: The worker.
        :rtype: JobWorker
        :raises KeyError: If no worker is named for the kind.
        """
        return self.by_kind[kind]


def job_workers(imports: ImportService, pages: PageService, processing: ProcessingJobs) -> JobWorkers:
    """Name the worker of every kind of job, each one method of a service of the request scope.

    :param imports: Import service of the scope, which imports the sources of a book.
    :type imports: ImportService
    :param pages: Page service of the scope, which writes the images of pending page versions.
    :type pages: PageService
    :param processing: The work of the processing jobs of the scope.
    :type processing: ProcessingJobs
    :returns: The workers by the kind of job.
    :rtype: JobWorkers
    """
    return JobWorkers(
        MappingProxyType(
            {
                JobKind.IMPORT_SOURCE: imports.run_import,
                JobKind.PREPARE_PAGES: pages.prepare_images,
                JobKind.RUN_STAGE: processing.run_stage,
                JobKind.PREVIEW_STEP: processing.preview_step,
                JobKind.CUT_TILES: processing.cut_tiles,
                JobKind.COLLECT_VERSIONS: processing.collect_versions,
                JobKind.MEASURE_BOOK: processing.measure_book,
                JobKind.DETECT_CONTENT: processing.detect_content,
            }
        )
    )


def job_runner(container: AsyncContainer) -> JobRunner:
    """Build the runner the job board is made with, which runs each job in a request scope inside a ``job`` span.

    The span is current while the job runs, so the spans of the work the job does become its children. The tracer comes
    from the application container, which a request scope is a child of, and the workers come from the scope.

    :param container: Application container, which provides the tracer and opens a request scope for every job.
    :type container: AsyncContainer
    :returns: The runner, which raises ``KeyError`` for a kind with no worker.
    :rtype: JobRunner
    """

    async def run(dir_name: BookDirectoryName, job: Job, control: JobControl) -> JobEnd:
        """Run the job through the worker of its kind, in a request scope and a span of its own.

        :param dir_name: Name of the directory of the book whose queue the job was taken from.
        :type dir_name: BookDirectoryName
        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which the worker lets through.
        :raises KeyError: If no worker is named for the kind of the job.
        """
        tracer = await container.get(Tracer)
        attributes = {SpanAttribute.JOB_KIND: job.kind, SpanAttribute.JOB_ID: str(job.id)}
        with tracer.span(SpanName.JOB, attributes, parent=SpanParent.NONE):
            async with container() as request:
                workers = await request.get(JobWorkers)
                return await workers.of(job.kind)(Book(dir_name), job, control)

    return run
