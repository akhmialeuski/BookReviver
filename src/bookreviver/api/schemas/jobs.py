"""Schemas of background jobs and of the events streamed to the browser.

Each server-sent event carries a name from ``EventName`` and exactly the data TanStack Query needs to patch or
invalidate the query it affects: the whole job for ``job-changed``, and only the project's identifier for
``project-changed``, since the client reads the project again.
"""

import enum
from datetime import datetime

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.ids import JobId, ProjectId


class EventName(enum.StrEnum):
    """Name of a server-sent event, which the browser listens to with ``addEventListener``."""

    JOB_CHANGED = 'job-changed'
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
