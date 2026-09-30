"""Builders of valid domain objects, so tests state only the fields they care about."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from bookreviver.domain.entities import Job, Page, PageVersion, Project, Scan, Source
from bookreviver.domain.enums import ColorMode, FileType, JobKind, JobState, PageOrigin, SourceKind, Stage
from bookreviver.domain.ids import AccountId, JobId, PageId, PageVersionId, ProjectId, ScanId, SourceId
from bookreviver.domain.values import BookDetails, ProcessorRef, ScanFacts, SourceFile

EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)
PAGE_WIDTH_PX: int = 2200
PAGE_HEIGHT_PX: int = 1561
SOURCE_SIZE_BYTES: int = 4096
SOURCE_SCAN_COUNT: int = 12
# The base step of a page cut from a whole scan, the split being skipped
SPLIT_NONE: ProcessorRef = ProcessorRef(key='split.none', version='1')
# Length of a page version identifier in hexadecimal digits
VERSION_ID_DIGITS: int = 16


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


def make_page(*, project_id: ProjectId, order_key: str = 'a0', scan: Scan | None = None) -> Page:
    """Build an included text page of the given project, cut from the whole of a scan or a placeholder without one.

    :param project_id: Project owning the page.
    :type project_id: ProjectId
    :param order_key: Order key placing the page in the book, unique within the project.
    :type order_key: str
    :param scan: Scan the page shows whole, or None for a placeholder.
    :type scan: Scan | None
    :returns: A page with a fresh identifier, created and updated at the epoch.
    :rtype: Page
    """
    return Page(
        id=PageId(uuid4()),
        project_id=project_id,
        order_key=order_key,
        origin=PageOrigin.PLACEHOLDER if scan is None else PageOrigin.SCAN,
        scan_id=None if scan is None else scan.id,
        created_at=EPOCH,
        updated_at=EPOCH,
    )


def make_source(*, project_id: ProjectId, name: str = 'book.pdf', minutes: int = 0) -> Source:
    """Build a PDF source of one file imported ``minutes`` after the epoch, whose digest follows from its name.

    :param project_id: Project owning the source.
    :type project_id: ProjectId
    :param name: Name of the uploaded file; sources of different names have different digests.
    :type name: str
    :param minutes: Minutes after ``EPOCH`` the source was imported.
    :type minutes: int
    :returns: A source with a fresh identifier and no import job.
    :rtype: Source
    """
    sha256 = hashlib.sha256(name.encode()).hexdigest()
    return Source(
        id=SourceId(uuid4()),
        project_id=project_id,
        kind=SourceKind.PDF,
        file_type=FileType.PDF,
        file_name=name,
        files=[SourceFile(name=name, size_bytes=SOURCE_SIZE_BYTES, sha256=sha256)],
        size_bytes=SOURCE_SIZE_BYTES,
        sha256=sha256,
        scan_count=SOURCE_SCAN_COUNT,
        imported_at=EPOCH + timedelta(minutes=minutes),
    )


def make_scan(*, source: Source, number: int) -> Scan:
    """Build a grayscale scan of a source.

    :param source: Source holding the scan, whose project the scan belongs to.
    :type source: Source
    :param number: Position of the scan in its source, starting at 0.
    :type number: int
    :returns: A scan with a fresh identifier, fixed pixel size and renditions not yet ready.
    :rtype: Scan
    """
    facts = ScanFacts(width_px=PAGE_WIDTH_PX, height_px=PAGE_HEIGHT_PX, color_mode=ColorMode.GRAY)
    return Scan(id=ScanId(uuid4()), project_id=source.project_id, source_id=source.id, number=number, facts=facts)


def make_page_version(*, page_id: PageId, minutes: int = 0) -> PageVersion:
    """Build the base version of a page cut from a whole scan, created ``minutes`` after the epoch.

    :param page_id: Page the version belongs to.
    :type page_id: PageId
    :param minutes: Minutes after ``EPOCH`` the version was created.
    :type minutes: int
    :returns: A pending ``split.none`` version with a random identifier and renditions not yet ready.
    :rtype: PageVersion
    """
    return PageVersion(
        id=PageVersionId(uuid4().hex[:VERSION_ID_DIGITS]),
        page_id=page_id,
        stage=Stage.PAGE_SPLIT,
        processor=SPLIT_NONE,
        created_at=EPOCH + timedelta(minutes=minutes),
    )


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
