"""Schemas of background jobs and of the events streamed to the browser.

Each server-sent event carries a name from ``EventName`` and exactly the data TanStack Query needs to patch or
invalidate the queries it affects: the whole job for ``job-changed``, the version and the parts written for
``book-changed``, the version alone for ``book-version`` and ``book-reset``, and the identifiers of a ready version for
``page-version-ready``. None of them names the book, which the browser named in the path of the request or the stream.
"""

import enum
from datetime import datetime

from fastapi import Query
from fastapi_pagination import Params

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.domain.books import BookResource
from bookreviver.domain.ids import JobId, PageId, PageVersionId
from bookreviver.domain.jobs import JobKind, JobState
from bookreviver.domain.stages import Stage


class EventName(enum.StrEnum):
    """Name of a server-sent event, which the browser listens to with ``addEventListener``."""

    JOB_CHANGED = 'job-changed'
    PAGE_VERSION_READY = 'page-version-ready'
    BOOK_CHANGED = 'book-changed'
    BOOK_RESET = 'book-reset'
    BOOK_VERSION = 'book-version'


class ProgressSchema(ResponseModel):
    """How far a job has come.

    :ivar done: Steps completed.
    :ivar total: Steps in all, zero while unknown.
    :ivar fraction: Completed share between 0 and 1, zero while the total is unknown.
    """

    done: int
    total: int
    fraction: float


class JobSchema(ResponseModel):
    """A background job, its state and its progress; also the data of a ``job-changed`` event.

    A job lives in the memory of the API process, so a job the process does not hold, such as one that ended before the
    process started, is answered as not found.

    :ivar id: Identifier of the job.
    :ivar kind: What the job does.
    :ivar stage: The stage a ``run-stage`` job runs, or None for any other kind of job.
    :ivar state: Where the job is in its life cycle.
    :ivar progress: How many of its tasks are done; for an import the files dealt with and the scans cut.
    :ivar error: Why the job failed, shown to the user, or empty.
    :ivar created_at: When the job was queued.
    :ivar started_at: When its book began to run the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    kind: JobKind
    stage: Stage | None
    state: JobState
    progress: ProgressSchema
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobQuery(Params):
    """The query of the list of jobs of a book: the page parameters, and whether to list running jobs alone.

    :ivar page: Number of the page of the list, from one.
    :ivar size: Number of jobs in one page of the list.
    :ivar active: Whether to list only the jobs that are queued or running.
    """

    active: bool = Query(default=False, description='List only the jobs that are queued or running')


class BookChangedSchema(ResponseModel):
    """A committed change of a book; the data of a ``book-changed`` event, whose id is the version.

    :ivar version: The version of the book after the change.
    :ivar resources: The parts of the book the change wrote, whose reads are the ones to repeat.
    """

    version: int
    resources: list[BookResource]


class BookVersionSchema(ResponseModel):
    """The version of a book when the browser connects; the data of ``book-version`` and ``book-reset`` events.

    ``book-version`` tells a browser that connects for the first time which version it holds. ``book-reset`` tells one
    that comes back that the book changed while it was not listening, and since the stream keeps no changes, every read
    of the book is to be repeated.

    :ivar version: The version of the book now.
    """

    version: int


class PageVersionReadySchema(ResponseModel):
    """A page version with its result; the data of a ``page-version-ready`` event.

    :ivar page_id: Identifier of the page.
    :ivar version_id: Identifier of the ready version to read again.
    """

    page_id: PageId
    version_id: PageVersionId
