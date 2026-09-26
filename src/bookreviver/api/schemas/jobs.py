"""Schemas of background jobs and of the events streamed to the browser."""

import enum
from datetime import datetime

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.ids import JobId, ProjectId


class EventName(enum.StrEnum):
    """Name of a server-sent event, which the browser listens to with ``addEventListener``."""

    JOB_CHANGED = 'job-changed'
    PAGE_READY = 'page-ready'
    PROJECT_CHANGED = 'project-changed'


class ProgressSchema(ResponseModel):
    """How far a job has come."""

    done: int
    total: int
    fraction: float


class JobSchema(ResponseModel):
    """A background job, its state and its progress; the data of a ``job-changed`` event."""

    id: JobId
    project_id: ProjectId
    kind: JobKind
    state: JobState
    progress: ProgressSchema
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PageReadySchema(ResponseModel):
    """A page whose images can be shown, with the asset version its image URLs carry; a ``page-ready`` event."""

    index: int
    version: int


class ProjectChangedSchema(ResponseModel):
    """A project whose description or source changed; the data of a ``project-changed`` event."""

    project_id: ProjectId
