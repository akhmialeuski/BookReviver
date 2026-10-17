"""Schemas of background jobs and of the events streamed to the browser.

Each server-sent event carries a name from ``EventName`` and exactly the data TanStack Query needs to patch or
invalidate the query it affects: the whole job for ``job-changed``, and only identifiers for every other event, since
the client reads again the project, the sources, the scan, the page manifest or the page version they name.
"""

import enum
from datetime import datetime

from fastapi import Query
from fastapi_pagination import Params

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.domain.enums import JobKind, JobState, PageChange, Stage, StageState
from bookreviver.domain.ids import JobId, PageId, PageVersionId, ProjectId, RecipeId, ScanId, SourceId


class EventName(enum.StrEnum):
    """Name of a server-sent event, which the browser listens to with ``addEventListener``."""

    JOB_CHANGED = 'job-changed'
    SOURCE_IMPORTED = 'source-imported'
    SCAN_READY = 'scan-ready'
    PAGES_CHANGED = 'pages-changed'
    PAGE_VERSION_READY = 'page-version-ready'
    PAGE_STAGE_CHANGED = 'page-stage-changed'
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

    A job lives in the memory of the API process, so a job the process does not hold, such as one that ended before the
    process started, is answered as not found.

    :ivar id: Identifier of the job.
    :ivar project_id: Project the job works on.
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
    project_id: ProjectId
    kind: JobKind
    stage: Stage | None
    state: JobState
    progress: ProgressSchema
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobQuery(Params):
    """The query of the list of jobs of a project: the page parameters, and whether to list running jobs alone.

    :ivar page: Number of the page of the list, from one.
    :ivar size: Number of jobs in one page of the list.
    :ivar active: Whether to list only the jobs that are queued or running.
    """

    active: bool = Query(default=False, description='List only the jobs that are queued or running')


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
    :ivar page_ids: Pages the change touched, one event naming all of them whatever the size of the group.
    :ivar change: What was done to them.
    """

    project_id: ProjectId
    page_ids: list[PageId]
    change: PageChange


class PageVersionReadySchema(ResponseModel):
    """A page version with its result; the data of a ``page-version-ready`` event.

    :ivar project_id: Identifier of the project owning the page.
    :ivar page_id: Identifier of the page.
    :ivar version_id: Identifier of the ready version to read again.
    """

    project_id: ProjectId
    page_id: PageId
    version_id: PageVersionId


class PageStageChangedSchema(ResponseModel):
    """A stage of a page whose current version or state changed; the data of a ``page-stage-changed`` event.

    :ivar project_id: Identifier of the project owning the page.
    :ivar page_id: Identifier of the page.
    :ivar stage: The stage that changed.
    :ivar recipe_id: Recipe the page was processed by, or None.
    :ivar head_version_id: The current version of the stage, or None.
    :ivar state: Whether the current version matches the inputs of the stage.
    :ivar through_step: Index in the recipe of the last step the page was run through when that is before the last step
                        that is on, or None.
    """

    project_id: ProjectId
    page_id: PageId
    stage: Stage
    recipe_id: RecipeId | None
    head_version_id: PageVersionId | None
    state: StageState
    through_step: int | None
