"""Builders of valid domain objects, so tests state only the fields they care about."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bookreviver.domain.entities import Job, Page, Project
from bookreviver.domain.enums import ColorMode, JobKind, JobState
from bookreviver.domain.ids import AccountId, JobId, ProjectId
from bookreviver.domain.values import BookDetails, PageFacts

EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)
PAGE_WIDTH_PX: int = 2200
PAGE_HEIGHT_PX: int = 1561


def new_account_id() -> AccountId:
    """Return a fresh account identifier.

    :returns: Random identifier of an account that exists nowhere else.
    :rtype: AccountId
    """
    return AccountId(uuid4())


def make_project(*, owner_id: AccountId, title: str = 'Book', minutes: int = 0) -> Project:
    """Build a project updated ``minutes`` after the epoch.

    :param owner_id: Account owning the project.
    :type owner_id: AccountId
    :param title: Title of the book.
    :type title: str
    :param minutes: Minutes after ``EPOCH`` the project was created and last updated.
    :type minutes: int
    :returns: A project with a fresh identifier and no source.
    :rtype: Project
    """
    moment = EPOCH + timedelta(minutes=minutes)
    return Project(
        id=ProjectId(uuid4()),
        owner_id=owner_id,
        details=BookDetails(title=title),
        created_at=moment,
        updated_at=moment,
    )


def make_page(*, project_id: ProjectId, index: int) -> Page:
    """Build a grayscale page of the given project.

    :param project_id: Project owning the page.
    :type project_id: ProjectId
    :param index: Position of the page in the book, starting at 0.
    :type index: int
    :returns: A page with fixed pixel size and assets not yet ready.
    :rtype: Page
    """
    facts = PageFacts(width_px=PAGE_WIDTH_PX, height_px=PAGE_HEIGHT_PX, color_mode=ColorMode.GRAY)
    return Page(project_id=project_id, index=index, facts=facts)


def make_job(*, project_id: ProjectId, state: JobState = JobState.QUEUED, minutes: int = 0) -> Job:
    """Build an import job created ``minutes`` after the epoch.

    :param project_id: Project the job imports into.
    :type project_id: ProjectId
    :param state: State of the job.
    :type state: JobState
    :param minutes: Minutes after ``EPOCH`` the job was created.
    :type minutes: int
    :returns: An import job with a fresh identifier.
    :rtype: Job
    """
    return Job(
        id=JobId(uuid4()),
        project_id=project_id,
        kind=JobKind.IMPORT_SOURCE,
        state=state,
        created_at=EPOCH + timedelta(minutes=minutes),
    )
