"""Schemas of background jobs and of the events streamed to the browser.

Each server-sent event carries a name from ``EventName`` and exactly the data TanStack Query needs to patch or
invalidate the query it affects: the whole job for ``job-changed``, and only identifiers for every other event, since
the client reads again the project, the sources, the scan, the page manifest or the page version they name.
"""

import enum
from datetime import datetime

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.ids import JobId, PageId, PageVersionId, ProjectId, ScanId, SourceId


class EventName(enum.StrEnum):
    """Name of a server-sent event, which the browser listens to with ``addEventListener``."""

    JOB_CHANGED = 'job-changed'
    SOURCE_IMPORTED = 'source-imported'
    SCAN_READY = 'scan-ready'
    PAGES_CHANGED = 'pages-changed'
    PAGE_VERSION_READY = 'page-version-ready'
    PROJECT_CHANGED = 'project-changed'


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

    :ivar id: Identifier of the job.
    :ivar project_id: Project the job works on.
    :ivar kind: What the job does.
    :ivar state: Where the job is in its life cycle.
    :ivar progress: How many of its steps are done.
    :ivar error: Why the job failed, shown to the user, or empty.
    :ivar created_at: When the job was recorded.
    :ivar started_at: When a worker started the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    project_id: ProjectId
    kind: JobKind
    state: JobState
    progress: ProgressSchema
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ProjectChangedSchema(ResponseModel):
    """A project whose description changed; the data of a ``project-changed`` event.

    :ivar project_id: Identifier of the project to read again.
    """

    project_id: ProjectId


class SourceImportedSchema(ResponseModel):
    """A source committed with its scans; the data of a ``source-imported`` event.

    :ivar project_id: Identifier of the project whose sources and counts to read again.
    :ivar source_id: Identifier of the new source.
    """

    project_id: ProjectId
    source_id: SourceId


class ScanReadySchema(ResponseModel):
    """A scan whose renditions can be shown; the data of a ``scan-ready`` event.

    :ivar project_id: Identifier of the project owning the scan.
    :ivar scan_id: Identifier of the scan to read again.
    """

    project_id: ProjectId
    scan_id: ScanId


class PagesChangedSchema(ResponseModel):
    """A book whose pages were added, removed, moved, numbered or given a kind; the data of a ``pages-changed`` event.

    :ivar project_id: Identifier of the project whose page manifest to read again.
    """

    project_id: ProjectId


class PageVersionReadySchema(ResponseModel):
    """A page version with its result; the data of a ``page-version-ready`` event.

    :ivar project_id: Identifier of the project owning the page.
    :ivar page_id: Identifier of the page.
    :ivar version_id: Identifier of the ready version to read again.
    """

    project_id: ProjectId
    page_id: PageId
    version_id: PageVersionId
