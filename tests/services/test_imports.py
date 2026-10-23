"""Tests for the import service: receiving an upload, and running the job it becomes, source by source."""

from contextlib import asynccontextmanager
from functools import partial
from operator import attrgetter, itemgetter
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.imaging.common import FactKey
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyScanRepository
from bookreviver.domain.entities import Actor, VersionInputs
from bookreviver.domain.enums import (
    BookResource,
    ColorMode,
    ContributorRole,
    DjvuDocumentKind,
    FileType,
    ImagePolicy,
    JobKind,
    JobState,
    LabelStyle,
    NumberDisplay,
    PageOrigin,
    RecipeKind,
    RejectionReason,
    Rendition,
    SourceKind,
    SourceStatus,
    Stage,
    UploadProblem,
    VersionState,
)
from bookreviver.domain.errors import (
    ConflictError,
    JobCancelledError,
    NotFoundError,
    UnsupportedSourceError,
    UploadRejectedError,
)
from bookreviver.domain.events import BookChanged, DomainEvent, JobChanged
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import Contributor, JobEnd, StageRun
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.runtime import JobControl
from bookreviver.services.base_versions import SPLIT_NONE
from bookreviver.services.imports import IMPORT_ACTIVE, NO_SOURCE_IMPORTED, ImportRun
from bookreviver.services.library import Library
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from bookreviver.services.sources import SOURCE_IMPORTING, SourceService
from tests.adapters.imaging.samples import (
    ROMAN_THEN_ARABIC_LABELS,
    ROMAN_THEN_ARABIC_RULES,
    DjvuPage,
    PdfPage,
    add_page_labels,
    add_xmp,
    requires_djvulibre,
    write_djvu_bundle,
    write_djvu_indirect,
    write_djvu_pages,
    write_pdf,
    xmp_packet,
)
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.builders import make_job, make_record, make_source, new_account_id
from tests.helpers.fakes_imports import (
    BOOK_WINDOW,
    DEFAULT_PARALLEL_SCANS,
    IMPORT_TIMEOUT_SECONDS,
    MAX_BYTES,
    MAX_FILES,
    OVERLAP_SECONDS,
    PAGE_WIDTH_PX,
    ImportRig,
    djvu_uploads,
    image_upload,
    pdf_upload,
    stored_book,
)
from tests.helpers.fakes_jobs import JobFakes
from tests.helpers.processors import AutoSplitProcessor
from tests.helpers.seeding import commit_record
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping, Sequence
    from pathlib import Path
    from typing import Any

    from fastapi import UploadFile

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import BookRecord, Job, Page, PageVersion, PaginationSection, Scan, Source
    from bookreviver.domain.ids import BookId, JobId, PageId, StorageKey
    from bookreviver.domain.values import SourceFile
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.storage import AssetStore, IncomingFile
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

RACING_UPLOADS: tuple[str, str] = ('first.jpg', 'second.jpg')
# The method of the source store that a test wraps to learn that an upload has received its files
STAGE_METHOD: str = 'stage'
INCOMING_UPLOADS: str = 'incoming/*'
FILES_ARG: str = 'files'
AUTO_SPLIT: str = 'split.auto'
CASE_ARG: str = 'case'
# The renditions cut from the ``full`` image, whatever its format
DERIVED: tuple[Rendition, ...] = (Rendition.PREVIEW, Rendition.THUMBNAIL, Rendition.TILES)
BILEVEL_MODE: str = '1'
GRAY_MODE: str = 'L'
RGB_MODE: str = 'RGB'
JPEG: str = 'JPEG'
PNG: str = 'PNG'
# Format and mode of the stored ``full`` image of a page of each colour under each policy, written out rather than
# taken from the rule the service applies
DJVU_FULL_IMAGES: dict[tuple[ImagePolicy, ColorMode], tuple[str, str]] = {
    (ImagePolicy.COMPACT, ColorMode.COLOR): (JPEG, RGB_MODE),
    (ImagePolicy.COMPACT, ColorMode.GRAY): (JPEG, GRAY_MODE),
    (ImagePolicy.COMPACT, ColorMode.BILEVEL): (PNG, BILEVEL_MODE),
    (ImagePolicy.LOSSLESS, ColorMode.COLOR): (PNG, RGB_MODE),
    (ImagePolicy.LOSSLESS, ColorMode.GRAY): (PNG, GRAY_MODE),
    (ImagePolicy.LOSSLESS, ColorMode.BILEVEL): (PNG, BILEVEL_MODE),
}
# Pages of the sample PDFs, and the scans they and the one image make in all
FIRST_PART_PAGES: int = 2
SECOND_PART_PAGES: int = 3
SCAN_COUNT: int = FIRST_PART_PAGES + SECOND_PART_PAGES + 1
SECOND_PART_WIDTH_PX: int = 210
SOURCE_COUNT: int = 3
THREE_PAGES: int = 3
FOUR_PAGES: int = 4
QUEUE_PDF: str = 'queue.pdf'
# The step of a run that cuts one scan, which the tests that steer the cutters replace
CUT_STEP: str = '_cut'
LONG_BOOK_PAGES: int = 6
PARALLEL_SCANS: int = 2
SECRET_FAULT: str = 'secret detail of the failure'
# Names of the uploaded files, each written once so a test compares a name with the one it uploaded
IMAGE_NAME: str = 'a.jpg'
PDF_NAME: str = 'a.pdf'
SECOND_PDF_NAME: str = 'b.pdf'
LABELLED_PDF_NAME: str = 'book.pdf'
FIRST_LABELLED_PDF_NAME: str = 'one.pdf'
SECOND_LABELLED_PDF_NAME: str = 'two.pdf'
COVER_NAME: str = 'cover.jpg'
PLATE_NAME: str = 'plate.jpg'
PAGE_JPEG_NAME: str = 'page.jpg'
PAGE_TIFF_NAME: str = 'page.tif'
UNREADABLE_PDF_NAME: str = 'bad.pdf'
TEXT_NAME: str = 'notes.txt'
PART_ONE_NAME: str = 'part1.pdf'
PART_TWO_NAME: str = 'part2.pdf'
PART_THREE_NAME: str = 'part3-cover.jpg'
DJVU_PART_TWO_NAME: str = 'part2.djvu'
DJVU_PART_TEN_NAME: str = 'part10.djvu'
DJVU_INDEX_NAME: str = 'a-index.djvu'
PDF_AFTER_DJVU_NAME: str = 'b-book.pdf'
ORIGINAL_PDF_NAME: str = 'a-book.pdf'
COPY_PDF_NAME: str = 'b-copy.pdf'
OTHER_IMAGE_NAME: str = 'c.jpg'
REPEATED_IMAGE_NAME: str = 'again.jpg'
# Two scans of one name in two folders, and the system files a folder upload brings along
FIRST_VOLUME_SCAN: str = 'vol1/001.tif'
SECOND_VOLUME_SCAN: str = 'vol2/001.tif'
VOLUME_SCAN_NAME: str = '001.tif'
FOLDER_SCAN: str = 'book/001.jpg'
FOLDER_VIEW_FILE: str = 'book/.DS_Store'
RESOURCE_FORK_FILE: str = 'book/._001.jpg'
THUMBNAILS_FILE: str = 'book/Thumbs.db'
AUTHOR_KEY: str = 'author'
FIRST_AUTHOR: str = 'First Author'
DAMAGED_PAGE_ERROR: str = f'Page 2 of {PDF_NAME} is damaged.'
REFUSED_SCANS_ERROR: str = 'scans refused'
# A file whose suffix names no type the application accepts, and a name for a file of an unknown format
UNKNOWN_TYPE_NAME: str = 'data.bin'
# The preview calls of a run on a PDF of three pages, cut one scan at a time, are the first scan's, then its page's, then
# the second scan's, which is where the worker of a test dies
CRASHING_PREVIEW: int = 3
# Roman pages, then a rule that writes a prefix and no number, which no section can express
PREFIX_ONLY_LABEL: str = '12a'
PREFIX_ONLY_RULES: tuple[Mapping[str, Any], ...] = (
    ROMAN_THEN_ARABIC_RULES[0],
    {'startpage': 2, 'prefix': PREFIX_ONLY_LABEL, 'style': '', 'firstpagenum': 1},
)
BROKEN_PDF: bytes = b'this is not a pdf'
DJVU_HEADER: bytes = b'AT&TFORM'
# Pages of the sample DjVu documents, of different widths so that a page taken for another shows in its scan
DJVU_PAGES: tuple[DjvuPage, ...] = (
    DjvuPage(size_px=(300, 400), mode=ColorMode.COLOR),
    DjvuPage(size_px=(250, 350), dpi=200),
    DjvuPage(size_px=(320, 420), dpi=600, mode=ColorMode.BILEVEL),
)
DJVU_WIDTHS: list[int] = [page.size_px[0] for page in DJVU_PAGES]


@pytest.fixture
def fx_rig(fx_storage_root: Path, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory) -> ImportRig:
    """Build the adapters an import runs on, over the storage root and the database of the test.

    :param fx_storage_root: Storage root under the test's temporary directory.
    :type fx_storage_root: Path
    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :returns: The rig with an empty database and nothing stored.
    :rtype: ImportRig
    """
    return ImportRig.build(fx_storage_root, fx_database, fx_uow)


@pytest.fixture
def fx_samples(tmp_path: Path) -> Path:
    """Return an empty directory the sample files of the test are built in.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: The existing directory.
    :rtype: Path
    """
    samples = tmp_path / 'samples'
    samples.mkdir()
    return samples


@pytest.fixture
def fx_owner() -> Actor:
    """Build the account that owns the book.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


@pytest.fixture
async def fx_book(fx_rig: ImportRig, fx_owner: Actor) -> BookRecord:
    """Store a book of ``fx_owner`` with no sources.

    :param fx_rig: Adapters of the import.
    :type fx_rig: ImportRig
    :param fx_owner: Account owning the book.
    :type fx_owner: Actor
    :returns: The stored book.
    :rtype: BookRecord
    """
    book_record = make_record()
    await commit_record(fx_rig.database, fx_rig.open_uow(), book_record, owner=fx_owner.account_id)
    return book_record


@pytest.fixture
def fx_book_files(fx_samples: Path) -> list[UploadFile]:
    """Build a book of two PDF parts and a separate cover, uploaded in an order other than the book's.

    :param fx_samples: Directory the sample files are built in.
    :type fx_samples: Path
    :returns: The cover first, then the second part, then the first part.
    :rtype: list[UploadFile]
    """
    return [
        image_upload(fx_samples, PART_THREE_NAME),
        pdf_upload(fx_samples, PART_TWO_NAME, pages=SECOND_PART_PAGES, width_px=SECOND_PART_WIDTH_PX),
        pdf_upload(fx_samples, PART_ONE_NAME, pages=FIRST_PART_PAGES),
    ]


def _tuned(
    rig: ImportRig,
    *,
    parallel_scans: int = DEFAULT_PARALLEL_SCANS,
    defaults: DefaultRecipes | None = None,
    processors: Sequence[Processor] | None = None,
) -> ImportRig:
    """Return the rig over a new board whose import jobs run on a service with other limits or recipes.

    The board of a rig runs its jobs on a service built with the settings' defaults, so a test that needs another limit
    or catalogue gets a board of its own over the same database, stores and event bus. A new board also holds no job,
    which is how a book looks after the process was restarted. A job that is not an import ends at once, since the
    split that an import queues is not the subject of the tests that use this rig.

    :param rig: Adapters of the import, whose database, stores and event bus the new rig keeps.
    :type rig: ImportRig
    :param parallel_scans: Largest number of scans the service cuts at once.
    :type parallel_scans: int
    :param defaults: The recipes the stages start with, or None for those of ``rig``.
    :type defaults: DefaultRecipes | None
    :param processors: The processors of the catalogue the recipes are checked against, or None for those of ``rig``.
    :type processors: Sequence[Processor] | None
    :returns: The rig whose board runs the service.
    :rtype: ImportRig
    """
    # The service is built by the rig and the board by its fakes, which the runner needs, so it finds the rig later
    built: list[ImportRig] = []

    async def run(job: Job, control: JobControl) -> JobEnd:
        """Run an import job on a service of the tuned rig, and let any other job succeed at once.

        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        """
        if job.kind is not JobKind.IMPORT_SOURCE:
            return JobEnd(state=JobState.SUCCEEDED)
        return await built[0].service(parallel_scans=parallel_scans).run_import(job, control)

    fakes = JobFakes(events=rig.events, clock=rig.fakes.clock, runner=run)
    tuned = evolve(
        rig,
        fakes=fakes,
        defaults=rig.defaults if defaults is None else defaults,
        processors=rig.processors if processors is None else processors,
    )
    built.append(tuned)
    return tuned


async def _import(rig: ImportRig, actor: Actor, book_id: BookId, files: list[UploadFile]) -> Job:
    """Upload the files and let the board run the job, as a request and the queue of the book would.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param actor: Account acting in the request.
    :type actor: Actor
    :param book_id: Book to import into.
    :type book_id: BookId
    :param files: The uploaded files.
    :type files: list[UploadFile]
    :returns: The job as the board holds it after the run.
    :rtype: Job
    """
    job = await rig.service().start_import(actor, book_id, files)
    await rig.fakes.board.run_queued()
    return rig.stored_job(job)


@asynccontextmanager
async def _running_import(rig: ImportRig, actor: Actor, book_id: BookId, files: list[UploadFile]) -> AsyncIterator[Job]:
    """Run an import on the board and keep it inside the inspection of its first source until the block ends.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param actor: Account acting in the request.
    :type actor: Actor
    :param book_id: Book to import into.
    :type book_id: BookId
    :param files: The uploaded files.
    :type files: list[UploadFile]
    :returns: Iterator yielding the running job once its run is held, and letting it go when the block ends.
    :rtype: AsyncIterator[Job]
    """
    job = await rig.service().start_import(actor, book_id, files)
    inspecting, released = anyio.Event(), anyio.Event()

    async def hold() -> None:
        """Say the run has come to the first source, and keep it there until the block of the test ends."""
        inspecting.set()
        await released.wait()

    rig.inspector.before_inspect = hold
    async with anyio.create_task_group() as group:
        group.start_soon(rig.fakes.board.run_queued)
        with anyio.fail_after(IMPORT_TIMEOUT_SECONDS):
            await inspecting.wait()
        try:
            yield rig.stored_job(job)
        finally:
            released.set()


async def _report(rig: ImportRig, book_id: BookId) -> list[Source]:
    """Read the rejected and skipped files of a book as committed, which are the report of its imports.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the files.
    :type book_id: BookId
    :returns: The files, in the order the imports recorded them.
    :rtype: list[Source]
    """
    return list(await rig.open_uow().read_book(book_id, lambda repos: repos.sources.list_unimported()))


async def _sources(rig: ImportRig, book_id: BookId) -> list[Source]:
    """Read the sources of a book as committed, in import order.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the sources.
    :type book_id: BookId
    :returns: The sources.
    :rtype: list[Source]
    """
    return (await stored_book(rig.open_uow(), book_id)).sources


async def _scans(rig: ImportRig, book_id: BookId) -> list[Scan]:
    """Read the scans of a book as committed, source by source.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the scans.
    :type book_id: BookId
    :returns: The scans.
    :rtype: list[Scan]
    """
    return (await stored_book(rig.open_uow(), book_id)).scans


async def _pages(rig: ImportRig, book_id: BookId) -> list[Page]:
    """Read the pages of a book as committed, in book order.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the pages.
    :type book_id: BookId
    :returns: The pages.
    :rtype: list[Page]
    """
    return (await stored_book(rig.open_uow(), book_id)).pages


async def _labels(rig: ImportRig, book_id: BookId) -> list[str]:
    """Read the labels the pages of a book show, in book order, as the sections committed give them.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the pages.
    :type book_id: BookId
    :returns: The labels.
    :rtype: list[str]
    """
    return (await stored_book(rig.open_uow(), book_id)).labels


async def _sections(rig: ImportRig, book_id: BookId) -> list[PaginationSection]:
    """Read the pagination sections of a book as committed, in the order they were made.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the sections.
    :type book_id: BookId
    :returns: The sections.
    :rtype: list[PaginationSection]
    """
    return (await stored_book(rig.open_uow(), book_id)).sections


async def _versions(rig: ImportRig, book_id: BookId, page_ids: Collection[PageId]) -> list[PageVersion]:
    """Read the versions of some pages as committed, page by page in the order the versions were created.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Book owning the pages.
    :type book_id: BookId
    :param page_ids: Pages owning the versions.
    :type page_ids: Collection[PageId]
    :returns: The versions.
    :rtype: list[PageVersion]
    """
    return await rig.open_uow().read_book(
        book_id,
        lambda repos: [version for page_id in page_ids for version in repos.page_versions.list_for_page(page_id)],
    )


async def _book_record(rig: ImportRig, book_id: BookId) -> BookRecord:
    """Read a book as committed.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :returns: The book.
    :rtype: BookRecord
    """
    return await rig.open_uow().read_book(book_id, lambda repos: repos.book_record.get(book_id))


async def _is_stored(assets: AssetStore, key: StorageKey) -> bool:
    """Return whether anything is stored at the key.

    :param assets: Asset store to look into.
    :type assets: AssetStore
    :param key: Key to look up.
    :type key: StorageKey
    :returns: True when a file or directory is stored there.
    :rtype: bool
    """
    try:
        async with assets.readable(key):
            return True
    except NotFoundError:
        return False


async def _record_with(rig: ImportRig, owner: Actor, policy: ImagePolicy) -> BookRecord:
    """Store a book of the owner with no sources, under an image policy.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param owner: Account owning the book.
    :type owner: Actor
    :param policy: Image policy of the book.
    :type policy: ImagePolicy
    :returns: The stored book.
    :rtype: BookRecord
    """
    book_record = evolve(make_record(), image_policy=policy)
    await commit_record(rig.database, rig.open_uow(), book_record, owner=owner.account_id)
    return book_record


async def _stored_image(rig: ImportRig, key: StorageKey) -> tuple[str | None, str]:
    """Read the format and the mode of a stored image, as Pillow reports them.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param key: Key of the stored image.
    :type key: StorageKey
    :returns: The Pillow format, such as ``PNG``, and the Pillow mode, such as ``1``.
    :rtype: tuple[str | None, str]
    """
    async with rig.assets.readable(key) as path:
        with Image.open(path) as image:
            return image.format, image.mode


def _events_of[EventT: DomainEvent](rig: ImportRig, kind: type[EventT]) -> list[EventT]:
    """Return the published events of one kind, in the order they were published.

    :param rig: Adapters of the import, whose event bus recorded the events.
    :type rig: ImportRig
    :param kind: Class of the events to return.
    :type kind: type[EventT]
    :returns: The events of that class.
    :rtype: list[EventT]
    """
    return [event for event in rig.fakes.events.published if isinstance(event, kind)]


def _cuts_a_scan(event: DomainEvent) -> bool:
    """Tell whether an event is the committed change that stored the images of a scan, the one change that writes versions.

    :param event: An event the unit of work announced.
    :type event: DomainEvent
    :returns: Whether it is such a change.
    :rtype: bool
    """
    return isinstance(event, BookChanged) and BookResource.VERSIONS in event.resources


class UploadCase(NamedTuple):
    """An upload that a rule refuses, with the problem it is refused for.

    :ivar names: Names of the uploaded files.
    :ivar problem: Rule the upload breaks.
    :ivar max_files: Largest number of files the service allows.
    :ivar max_bytes: Largest total size the service allows, in bytes.
    """

    names: list[str | None]
    problem: UploadProblem
    max_files: int = MAX_FILES
    max_bytes: int = MAX_BYTES


REFUSED_UPLOADS: list[UploadCase] = [
    UploadCase(names=[], problem=UploadProblem.NO_FILES),
    UploadCase(names=['a.jpg', 'b.jpg', 'c.jpg'], problem=UploadProblem.TOO_MANY_FILES, max_files=2),
    UploadCase(names=['a.jpg'], problem=UploadProblem.TOO_LARGE, max_bytes=3),
    UploadCase(names=[None], problem=UploadProblem.EMPTY_NAME),
    UploadCase(names=['a.jpg', 'A.JPG'], problem=UploadProblem.DUPLICATE_NAME),
    UploadCase(names=['vol1/a.jpg', 'VOL1\\A.JPG'], problem=UploadProblem.DUPLICATE_NAME),
    UploadCase(names=['a.jpg', '../b.jpg'], problem=UploadProblem.UNSAFE_PATH),
    UploadCase(names=['/etc/a.jpg'], problem=UploadProblem.UNSAFE_PATH),
    UploadCase(names=['C:\\scans\\a.jpg'], problem=UploadProblem.UNSAFE_PATH),
]
REFUSED_IDS: list[str] = [
    'no-files',
    'too-many-files',
    'too-large',
    'empty-name',
    'duplicate-name',
    'duplicate-path-in-a-folder',
    'parent-folder',
    'absolute-path',
    'drive-letter',
]


class FullFormatCase(NamedTuple):
    """A page image imported under an image policy, and the ``full`` image it must be stored as.

    :ivar policy: Image policy of the book.
    :ivar mode: Pillow mode of the uploaded page image.
    :ivar name: Name of the uploaded file, whose suffix selects its format.
    :ivar expected: Format the ``full`` image must be recorded and stored in.
    :ivar stored: Pillow format and mode the stored ``full`` file must have.
    """

    policy: ImagePolicy
    mode: str
    name: str
    expected: Rendition

    @property
    def stored(self) -> tuple[str, str]:
        """The Pillow format and mode of the stored file: the format of ``expected``, in the mode it was uploaded in."""
        return (PNG if self.expected is Rendition.FULL_PNG else JPEG), self.mode


class BadFile(NamedTuple):
    """A file no source can be made of, with the reason an import rejects it for.

    :ivar name: Name of the file.
    :ivar content: Content of the file.
    :ivar reason: Reason the file is rejected for.
    """

    name: str
    content: bytes
    reason: RejectionReason


BAD_FILES: list[BadFile] = [
    BadFile(name='bad.pdf', content=BROKEN_PDF, reason=RejectionReason.UNREADABLE),
    BadFile(name='bad.jpg', content=BROKEN_PDF, reason=RejectionReason.UNREADABLE),
    BadFile(name='book.djvu', content=DJVU_HEADER, reason=RejectionReason.UNREADABLE),
    BadFile(name='notes.txt', content=b'plain text', reason=RejectionReason.UNSUPPORTED_TYPE),
    BadFile(name='Thumbs.db', content=b'thumbnails', reason=RejectionReason.SYSTEM_FILE),
    BadFile(name='vol1/.DS_Store', content=b'folder view', reason=RejectionReason.SYSTEM_FILE),
    # The suffix of the image it accompanies, which no reader could make a source of
    BadFile(name='._page.jpg', content=b'resource fork', reason=RejectionReason.SYSTEM_FILE),
]
BAD_FILE_IDS: list[str] = [
    'damaged-pdf',
    'damaged-image',
    'truncated-djvu',
    'unsupported-type',
    'thumbs-db',
    'ds-store-in-folder',
    'apple-double',
]


class TestStartImport:
    """Tests for ImportService.start_import()."""

    async def test_stages_the_upload_and_puts_a_queued_job_with_its_files_on_the_board(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify the upload is staged for the job, the job records its files in upload order, and the board holds it.

        Nothing is written to the database, and the job has not begun to run.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await fx_rig.service().start_import(fx_owner, fx_book.id, fx_book_files)

        async with fx_rig.sources.staged_files(fx_book.id, job.id) as staged:
            staged_names = sorted(staged)
        expect(fx_rig.stored_job(job) == job)
        expect(fx_rig.fakes.board.put_jobs == [job])
        expect((job.kind, job.state, job.progress.total) == (JobKind.IMPORT_SOURCE, JobState.QUEUED, 0))
        expect(
            job.request is not None
            and [file.name for file in job.request.files]
            == [
                PART_THREE_NAME,
                PART_TWO_NAME,
                PART_ONE_NAME,
            ]
        )
        expect(staged_names == [PART_ONE_NAME, PART_TWO_NAME, PART_THREE_NAME])
        expect(await stored_book(fx_rig.open_uow(), fx_book.id) == ([], [], [], []))
        assert_expectations()

    async def test_book_of_another_account_is_not_found(
        self, fx_rig: ImportRig, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify an account cannot import into, or learn about, a book it does not own.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_book: Book of another account.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        stranger = Actor(account_id=new_account_id())
        with pytest.raises(NotFoundError):
            await fx_rig.service().start_import(stranger, fx_book.id, [image_upload(fx_samples, IMAGE_NAME)])
        assert fx_rig.fakes.board.put_jobs == []

    async def test_book_with_an_import_queued_raises_conflict_before_receiving_anything(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify a second upload is refused while an import is queued, before its files are streamed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root of the test, which holds the database of the book as well.
        :type fx_storage_root: Path
        """
        queued = fx_rig.fakes.board.put(make_job(book_id=fx_book.id))

        with pytest.raises(ConflictError, match=IMPORT_ACTIVE):
            await fx_rig.service().start_import(fx_owner, fx_book.id, [image_upload(fx_samples, IMAGE_NAME)])

        expect(fx_rig.fakes.board.put_jobs == [queued])
        expect(not await anyio.Path(fx_storage_root / BookKeys(fx_book.id).incoming_area).exists())
        assert_expectations()

    async def test_book_with_an_import_running_raises_conflict_and_leaves_only_the_upload_of_that_import(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify a second upload is refused while an import is running, and its upload is not kept.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root under the test's temporary directory.
        :type fx_storage_root: Path
        """
        first = [image_upload(fx_samples, IMAGE_NAME)]
        async with _running_import(fx_rig, fx_owner, fx_book.id, first) as running:
            with pytest.raises(ConflictError, match=IMPORT_ACTIVE):
                await fx_rig.service().start_import(fx_owner, fx_book.id, [image_upload(fx_samples, 'again.png')])

            expect(running.state is JobState.RUNNING)
            expect([job.id for job in fx_rig.fakes.board.put_jobs] == [running.id])
            expect(len([path async for path in anyio.Path(fx_storage_root).rglob(INCOMING_UPLOADS)]) == 1)
        assert_expectations()

    async def test_finished_import_does_not_keep_the_book_from_importing_again(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a book whose last import ended accepts a new upload.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        earlier = await _import(fx_rig, fx_owner, fx_book.id, [image_upload(fx_samples, IMAGE_NAME)])

        job = await fx_rig.service().start_import(
            fx_owner, fx_book.id, [image_upload(fx_samples, COVER_NAME, width_px=SECOND_PART_WIDTH_PX)]
        )

        expect(earlier.state is JobState.SUCCEEDED)
        expect(job.state is JobState.QUEUED)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, REFUSED_UPLOADS, ids=REFUSED_IDS)
    async def test_upload_that_breaks_a_rule_is_refused_and_leaves_nothing(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, case: UploadCase
    ) -> None:
        """Verify each upload rule refuses with its problem, and no job or upload is left.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param case: Names of the files and limits of an upload, with the rule it breaks.
        :type case: UploadCase
        """
        files = [
            upload(name, content=image_upload(fx_samples, f'{index}.jpg').file.read())
            for index, name in enumerate(case.names)
        ]

        with pytest.raises(UploadRejectedError) as refusal:
            await fx_rig.service(max_files=case.max_files, max_bytes=case.max_bytes).start_import(
                fx_owner, fx_book.id, files
            )

        expect(refusal.value.problem is case.problem)
        expect(fx_rig.fakes.board.put_jobs == [])
        expect(fx_rig.fakes.board.jobs(fx_book.id, active=False) == [])
        assert_expectations()


class TestAuthorizeUpload:
    """Tests for ImportService.authorize_upload(), which the API runs before it reads the body of a request."""

    async def test_owner_of_an_idle_book_may_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord
    ) -> None:
        """Verify the owner of a book with no import running is allowed, and nothing is put on the board.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        """
        await fx_rig.service().authorize_upload(fx_owner, fx_book.id)

        expect(fx_rig.fakes.board.put_jobs == [])
        expect(fx_rig.fakes.board.jobs(fx_book.id, active=False) == [])
        assert_expectations()

    async def test_another_account_is_told_the_book_does_not_exist(
        self, fx_rig: ImportRig, fx_book: BookRecord
    ) -> None:
        """Verify an account that does not own the book learns nothing about it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_book: Book of another account.
        :type fx_book: BookRecord
        """
        with pytest.raises(NotFoundError):
            await fx_rig.service().authorize_upload(Actor(account_id=new_account_id()), fx_book.id)

    async def test_book_that_imports_is_a_conflict(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord
    ) -> None:
        """Verify a book with an import on the board is refused with the conflict of the upload rule.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        """
        fx_rig.fakes.board.put(make_job(book_id=fx_book.id))

        with pytest.raises(ConflictError, match=IMPORT_ACTIVE):
            await fx_rig.service().authorize_upload(fx_owner, fx_book.id)


class TestRunImport:
    """Tests for ImportService.run_import()."""

    async def test_imports_every_file_as_a_source_of_its_own_with_its_pages_in_book_order(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify two PDF parts and a cover become three sources, and their scans become pages in the upload order.

        The files are uploaded out of the order of their names, and the book follows the upload.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await _import(fx_rig, fx_owner, fx_book.id, fx_book_files)

        sources, scans, pages = (
            await _sources(fx_rig, fx_book.id),
            await _scans(fx_rig, fx_book.id),
            await _pages(fx_rig, fx_book.id),
        )
        by_id = {scan.id: scan for scan in scans}
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == [PART_THREE_NAME, PART_TWO_NAME, PART_ONE_NAME])
        expect(all(source.status is SourceStatus.IMPORTED for source in sources))
        expect([source.scan_count for source in sources] == [1, SECOND_PART_PAGES, FIRST_PART_PAGES])
        expect(await _report(fx_rig, fx_book.id) == [])
        expect(len(scans) == len(pages) == SCAN_COUNT)
        expect(all(page.origin is PageOrigin.SCAN and page.scan_id in by_id for page in pages))
        expect(
            [(by_id[page.scan_id].source_id, by_id[page.scan_id].number) for page in pages if page.scan_id]
            == [(source.id, number) for source in sources for number in range(source.scan_count)]
        )
        assert_expectations()

    async def test_files_of_different_folders_with_one_name_are_two_sources_named_by_their_paths(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify ``vol1/001.tif`` and ``vol2/001.tif`` are two sources, kept under their paths and in upload order.

        Each source stores its file under the base name in a directory of its own, so the same name never clashes.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        second = image_upload(fx_samples, 'second.tif', width_px=SECOND_PART_WIDTH_PX)
        first = image_upload(fx_samples, 'first.tif')
        uploads = [
            upload(SECOND_VOLUME_SCAN, content=second.file.read()),
            upload(FIRST_VOLUME_SCAN, content=first.file.read()),
        ]

        job = await _import(fx_rig, fx_owner, fx_book.id, uploads)

        sources = await _sources(fx_rig, fx_book.id)
        stored = []
        for source in sources:
            async with fx_rig.sources.source_files(fx_book.id, source.id) as files:
                stored.append([path.name for path in files])
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == [SECOND_VOLUME_SCAN, FIRST_VOLUME_SCAN])
        expect(
            [[file.name for file in source.files] for source in sources] == [[SECOND_VOLUME_SCAN], [FIRST_VOLUME_SCAN]]
        )
        expect(stored == [[VOLUME_SCAN_NAME], [VOLUME_SCAN_NAME]])
        expect(await _report(fx_rig, fx_book.id) == [])
        assert_expectations()

    async def test_system_files_of_a_directory_are_rejected_and_do_not_stop_the_import(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify the files an operating system adds to a folder are rejected as such, in upload order, and the rest imports.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        scan = image_upload(fx_samples, 'scan.jpg')
        uploads = [
            upload(FOLDER_VIEW_FILE, content=b'folder view'),
            upload(FOLDER_SCAN, content=scan.file.read()),
            upload(RESOURCE_FORK_FILE, content=b'resource fork'),
            upload(THUMBNAILS_FILE, content=b'thumbnails'),
        ]

        job = await _import(fx_rig, fx_owner, fx_book.id, uploads)

        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in await _sources(fx_rig, fx_book.id)] == [FOLDER_SCAN])
        expect(
            [(file.file_name, file.status, file.rejection) for file in await _report(fx_rig, fx_book.id)]
            == [
                (FOLDER_VIEW_FILE, SourceStatus.REJECTED, RejectionReason.SYSTEM_FILE),
                (RESOURCE_FORK_FILE, SourceStatus.REJECTED, RejectionReason.SYSTEM_FILE),
                (THUMBNAILS_FILE, SourceStatus.REJECTED, RejectionReason.SYSTEM_FILE),
            ]
        )
        assert_expectations()

    async def test_takes_the_page_labels_of_a_pdf_as_the_labels_of_its_scans_and_pages(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a PDF with Roman numerals for its preface and Arabic ones after gives its scans and pages those labels.

        An image file carries no labels, so its page stays unnumbered, whether it comes before the PDF or after it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, LABELLED_PDF_NAME, pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / LABELLED_PDF_NAME, rules=ROMAN_THEN_ARABIC_RULES)
        files = [upload(LABELLED_PDF_NAME, content=labelled.read_bytes()), image_upload(fx_samples, COVER_NAME)]

        await _import(fx_rig, fx_owner, fx_book.id, files)

        scans = await _scans(fx_rig, fx_book.id)
        expected = [*ROMAN_THEN_ARABIC_LABELS, '']
        expect([scan.source_label for scan in scans] == expected)
        expect(await _labels(fx_rig, fx_book.id) == expected)
        assert_expectations()

    async def test_makes_a_pagination_section_of_each_page_label_rule_of_a_pdf(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify the Roman and the Arabic rules become two printed sections that start at the pages the rules do.

        The labels the sections give are the labels of the file, so no page keeps its label as an exception.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, LABELLED_PDF_NAME, pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / LABELLED_PDF_NAME, rules=ROMAN_THEN_ARABIC_RULES)

        await _import(fx_rig, fx_owner, fx_book.id, [upload(LABELLED_PDF_NAME, content=labelled.read_bytes())])

        pages, sections = await _pages(fx_rig, fx_book.id), await _sections(fx_rig, fx_book.id)
        by_start = {section.first_page_id: section for section in sections}
        expect(len(sections) == len(ROMAN_THEN_ARABIC_RULES))
        expect(by_start[pages[0].id].style is LabelStyle.ROMAN_LOWER)
        expect(by_start[pages[2].id].style is LabelStyle.ARABIC)
        expect({section.display for section in sections} == {NumberDisplay.PRINTED})
        expect([page.manual_label is not None for page in pages] == [False] * len(pages))
        assert_expectations()

    async def test_keeps_a_page_label_no_section_gives_as_an_exception(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a rule that writes a prefix and no number is a section that does not count, and its labels stay by hand.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, LABELLED_PDF_NAME, pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / LABELLED_PDF_NAME, rules=PREFIX_ONLY_RULES)

        await _import(fx_rig, fx_owner, fx_book.id, [upload(LABELLED_PDF_NAME, content=labelled.read_bytes())])

        pages, sections = await _pages(fx_rig, fx_book.id), await _sections(fx_rig, fx_book.id)
        expect(await _labels(fx_rig, fx_book.id) == ['i', 'ii', PREFIX_ONLY_LABEL, PREFIX_ONLY_LABEL])
        expect([page.manual_label is not None for page in pages] == [False, False, True, True])
        expect(
            sorted(section.display for section in sections)
            == sorted([NumberDisplay.PRINTED, NumberDisplay.NOT_COUNTED])
        )
        assert_expectations()

    async def test_a_source_without_page_labels_does_not_continue_the_numbering_of_a_labelled_pdf(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify an image that follows a labelled PDF is outside the count, in a section of its own that does not count.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, LABELLED_PDF_NAME, pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / LABELLED_PDF_NAME, rules=ROMAN_THEN_ARABIC_RULES)
        files = [upload(LABELLED_PDF_NAME, content=labelled.read_bytes()), image_upload(fx_samples, PLATE_NAME)]

        await _import(fx_rig, fx_owner, fx_book.id, files)

        pages, sections = await _pages(fx_rig, fx_book.id), await _sections(fx_rig, fx_book.id)
        [stopper] = [section for section in sections if section.first_page_id == pages[-1].id]
        expect(await _labels(fx_rig, fx_book.id) == [*ROMAN_THEN_ARABIC_LABELS, ''])
        expect(stopper.display is NumberDisplay.NOT_COUNTED)
        expect(len(sections) == len(ROMAN_THEN_ARABIC_RULES) + 1)
        assert_expectations()

    async def test_a_book_without_sections_stays_without_them_when_a_source_has_no_page_labels(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify images alone make no section, so the book is numbered when the user adds its first one.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        files = [
            image_upload(fx_samples, COVER_NAME),
            image_upload(fx_samples, PLATE_NAME, width_px=PAGE_WIDTH_PX + 1),
        ]

        await _import(fx_rig, fx_owner, fx_book.id, files)

        assert await _sections(fx_rig, fx_book.id) == []

    async def test_a_later_source_with_page_labels_makes_sections_of_its_own(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a labelled PDF imported after another one gets its own sections, starting at its own first page.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, FIRST_LABELLED_PDF_NAME, pages=len(ROMAN_THEN_ARABIC_LABELS))
        first = add_page_labels(fx_samples / FIRST_LABELLED_PDF_NAME, rules=ROMAN_THEN_ARABIC_RULES)
        pdf_upload(
            fx_samples, SECOND_LABELLED_PDF_NAME, pages=len(ROMAN_THEN_ARABIC_LABELS), width_px=PAGE_WIDTH_PX + 1
        )
        second = add_page_labels(fx_samples / SECOND_LABELLED_PDF_NAME, rules=ROMAN_THEN_ARABIC_RULES)

        await _import(fx_rig, fx_owner, fx_book.id, [upload(FIRST_LABELLED_PDF_NAME, content=first.read_bytes())])
        await _import(fx_rig, fx_owner, fx_book.id, [upload(SECOND_LABELLED_PDF_NAME, content=second.read_bytes())])

        pages, sections = await _pages(fx_rig, fx_book.id), await _sections(fx_rig, fx_book.id)
        starts = {section.first_page_id for section in sections}
        size = len(ROMAN_THEN_ARABIC_LABELS)
        expect(len(sections) == 2 * len(ROMAN_THEN_ARABIC_RULES))
        expect(starts == {pages[0].id, pages[2].id, pages[size].id, pages[size + 2].id})
        expect(await _labels(fx_rig, fx_book.id) == [*ROMAN_THEN_ARABIC_LABELS, *ROMAN_THEN_ARABIC_LABELS])
        assert_expectations()

    async def test_writes_the_renditions_of_every_scan_and_the_base_version_of_every_page(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify each scan has its four renditions and each page a ready ``split.none`` version with its own four.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        await _import(fx_rig, fx_owner, fx_book.id, fx_book_files)

        keys = BookKeys(fx_book.id)
        scans, pages = await _scans(fx_rig, fx_book.id), await _pages(fx_rig, fx_book.id)
        missing: list[StorageKey] = []
        for scan in scans:
            missing += [
                key
                for rendition in (scan.renditions.full, *DERIVED)
                if not await _is_stored(fx_rig.assets, key := keys.scan_rendition(scan, rendition))
            ]
        versions = await _versions(fx_rig, fx_book.id, [page.id for page in pages])
        for version in versions:
            missing += [
                key
                for rendition in (Rendition.FULL_JPEG, *DERIVED)
                if not await _is_stored(fx_rig.assets, key := keys.version_rendition(version, rendition))
            ]
        expect(missing == [])
        expect(all(scan.renditions.ready for scan in scans))
        expect(len(versions) == len(pages))
        expect(
            all(
                (version.stage, version.processor, version.state, version.input_id)
                == (Stage.PAGE_SPLIT, SPLIT_NONE, VersionState.READY, None)
                and version.renditions is not None
                and version.renditions.ready
                and version.id == VersionInputs(page_id=version.page_id, processor=SPLIT_NONE).identify()
                for version in versions
            )
        )
        assert_expectations()

    async def test_cuts_every_pyramid_into_the_directory_of_its_key(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify each pyramid is cut into the directory of its own key, for scans and pages alike.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        await _import(fx_rig, fx_owner, fx_book.id, [image_upload(fx_samples, COVER_NAME)])

        keys = BookKeys(fx_book.id)
        [scan] = await _scans(fx_rig, fx_book.id)
        [page] = await _pages(fx_rig, fx_book.id)
        [version] = await _versions(fx_rig, fx_book.id, [page.id])
        # The asset store hands out a hidden directory beside the key, which the pyramid is published from
        assert sorted(target.parent for target in fx_rig.tiler.targets) == sorted(
            [
                (fx_storage_root / keys.scan_rendition(scan, Rendition.TILES)).parent,
                (fx_storage_root / keys.version_rendition(version, Rendition.TILES)).parent,
            ]
        )

    async def test_commits_each_source_before_any_scan_and_announces_the_end_of_the_job(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_book_files: list[UploadFile],
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify each source is one change before the first scan is, each scan one change, and the job ends last.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        fx_book_changes.published.clear()
        job = await _import(fx_rig, fx_owner, fx_book.id, fx_book_files)

        published = fx_rig.fakes.events.published
        jobs = _events_of(fx_rig, JobChanged)
        changes = fx_book_changes.changed_parts()
        sources = [index for index, parts in enumerate(changes) if BookResource.PAGES in parts]
        cuts = [index for index, parts in enumerate(changes) if BookResource.VERSIONS in parts]
        expect((len(sources), len(cuts)) == (SOURCE_COUNT, SCAN_COUNT))
        # Every source is committed before the first image is, so the browser knows the book before it is cut
        expect(max(sources) < min(cuts))
        expect(jobs[0].job.state is JobState.RUNNING)
        expect(jobs[-1].job == job and job.state is JobState.SUCCEEDED)
        expect(published[-1] == jobs[-1])
        assert_expectations()

    async def test_counts_the_files_and_the_scans_in_the_progress_of_the_job(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify the job's total is the number of files plus the number of scans of the upload, and done reaches it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await _import(fx_rig, fx_owner, fx_book.id, fx_book_files)

        progresses = [(event.job.progress.done, event.job.progress.total) for event in _events_of(fx_rig, JobChanged)]
        expect((job.progress.done, job.progress.total) == (SOURCE_COUNT + SCAN_COUNT, SOURCE_COUNT + SCAN_COUNT))
        expect(progresses == sorted(progresses, key=itemgetter(0)))
        expect(job.started_at is not None and job.finished_at is not None)
        assert_expectations()

    async def test_new_pages_join_the_end_of_a_book_that_has_pages(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a second upload appends its pages after the pages of the first, in upload order.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        await _import(fx_rig, fx_owner, fx_book.id, [pdf_upload(fx_samples, 'first.pdf', pages=2)])
        before = await _pages(fx_rig, fx_book.id)

        await _import(fx_rig, fx_owner, fx_book.id, [pdf_upload(fx_samples, 'second.pdf', pages=2, width_px=210)])

        pages = await _pages(fx_rig, fx_book.id)
        assert (pages[: len(before)], len(pages)) == (before, 2 * len(before))


class TestRunImportFullFormat:
    """Tests for the format ImportService.run_import() writes the ``full`` image of a scan and its pages in."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            FullFormatCase(ImagePolicy.COMPACT, BILEVEL_MODE, PAGE_TIFF_NAME, Rendition.FULL_PNG),
            FullFormatCase(ImagePolicy.LOSSLESS, BILEVEL_MODE, PAGE_TIFF_NAME, Rendition.FULL_PNG),
            FullFormatCase(ImagePolicy.COMPACT, GRAY_MODE, PAGE_JPEG_NAME, Rendition.FULL_JPEG),
            FullFormatCase(ImagePolicy.LOSSLESS, GRAY_MODE, PAGE_JPEG_NAME, Rendition.FULL_PNG),
            FullFormatCase(ImagePolicy.COMPACT, RGB_MODE, PAGE_JPEG_NAME, Rendition.FULL_JPEG),
            FullFormatCase(ImagePolicy.LOSSLESS, RGB_MODE, PAGE_JPEG_NAME, Rendition.FULL_PNG),
        ],
        ids=lambda case: f'{case.policy}-{case.mode}',
    )
    async def test_writes_scan_and_page_version_in_the_format_the_colour_and_the_policy_choose(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path, case: FullFormatCase
    ) -> None:
        """Verify the format is asked of the rasterizer, recorded with the scan and its page's version, and stored.

        The preview, the thumbnail and the pyramid are cut from that file, and only a file of that format exists.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param case: Image policy of the book, the page image to import, and the format its ``full`` must have.
        :type case: FullFormatCase
        """
        book_record = await _record_with(fx_rig, fx_owner, case.policy)

        await _import(fx_rig, fx_owner, book_record.id, [image_upload(fx_samples, case.name, mode=case.mode)])

        keys = BookKeys(book_record.id)
        [scan] = await _scans(fx_rig, book_record.id)
        [page] = await _pages(fx_rig, book_record.id)
        [version] = await _versions(fx_rig, book_record.id, [page.id])
        other = Rendition.FULL_JPEG if case.expected is Rendition.FULL_PNG else Rendition.FULL_PNG
        expect(fx_rig.rasterizer.formats == [case.expected])
        expect(scan.renditions.full is case.expected)
        expect(version.renditions is not None and version.renditions.full is case.expected)
        for of in (partial(keys.scan_rendition, scan), partial(keys.version_rendition, version)):
            expect(all([await _is_stored(fx_rig.assets, of(rendition)) for rendition in (case.expected, *DERIVED)]))
            expect(not await _is_stored(fx_rig.assets, of(other)))
        expect(await _stored_image(fx_rig, keys.scan_rendition(scan, case.expected)) == case.stored)
        assert_expectations()

    async def test_later_change_of_the_policy_leaves_the_stored_images_and_their_paths_alone(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path
    ) -> None:
        """Verify a scan imported under ``compact`` stays a JPEG after the book turns ``lossless``.

        A scan imported afterwards follows the new policy, and the first keeps its recorded format, its key and its
        file, so no stored address changes.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        book_record = await _record_with(fx_rig, fx_owner, ImagePolicy.COMPACT)
        await _import(fx_rig, fx_owner, book_record.id, [image_upload(fx_samples, 'first.jpg')])
        [first] = await _scans(fx_rig, book_record.id)
        await fx_rig.open_uow().change_book(
            book_record.id,
            lambda repos, _book_record: repos.book_record.update(
                evolve(book_record, image_policy=ImagePolicy.LOSSLESS)
            ),
        )

        second = image_upload(fx_samples, 'second.jpg', width_px=SECOND_PART_WIDTH_PX)
        await _import(fx_rig, fx_owner, book_record.id, [second])

        keys = BookKeys(book_record.id)
        scans = await _scans(fx_rig, book_record.id)
        kept = next(scan for scan in scans if scan.id == first.id)
        added = next(scan for scan in scans if scan.id != first.id)
        expect(kept.renditions.full is Rendition.FULL_JPEG)
        expect(added.renditions.full is Rendition.FULL_PNG)
        expect(await _is_stored(fx_rig.assets, keys.scan_rendition(kept, Rendition.FULL_JPEG)))
        expect(not await _is_stored(fx_rig.assets, keys.scan_rendition(kept, Rendition.FULL_PNG)))
        assert_expectations()


@requires_djvulibre
class TestRunImportDjvu:
    """Tests for the sources ImportService.run_import() makes of DjVu files."""

    @pytest.mark.parametrize('policy', list(ImagePolicy))
    async def test_stores_a_bilevel_page_as_a_one_bit_png_under_both_policies(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path, policy: ImagePolicy
    ) -> None:
        """Verify a bilevel DjVu page is a 1-bit PNG whatever the policy, and a gray or colour page follows it.

        The pages of the scans and of their base versions are checked, so the copy keeps the format.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param policy: Image policy of the book.
        :type policy: ImagePolicy
        """
        book_record = await _record_with(fx_rig, fx_owner, policy)
        bundle = write_djvu_bundle(fx_samples / 'book.djvu', pages=DJVU_PAGES)

        job = await _import(fx_rig, fx_owner, book_record.id, djvu_uploads([bundle]))

        keys = BookKeys(book_record.id)
        scans = await _scans(fx_rig, book_record.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(sorted(scan.facts.color_mode for scan in scans) == sorted(page.mode for page in DJVU_PAGES))
        for scan in scans:
            wanted = DJVU_FULL_IMAGES[policy, scan.facts.color_mode]
            expect(await _stored_image(fx_rig, keys.scan_rendition(scan, scan.renditions.full)) == wanted)
        for page in await _pages(fx_rig, book_record.id):
            [version] = await _versions(fx_rig, book_record.id, [page.id])
            scan = next(scan for scan in scans if scan.id == page.scan_id)
            wanted = DJVU_FULL_IMAGES[policy, scan.facts.color_mode]
            expect(version.renditions is not None and version.renditions.full is scan.renditions.full)
            expect(await _stored_image(fx_rig, keys.version_rendition(version, scan.renditions.full)) == wanted)
        assert_expectations()

    async def test_keeps_an_indirect_document_as_one_source_with_its_scans_in_index_order(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify an index and its page files become one source of all four files, next to the PDF of the upload.

        The files are uploaded out of order. The source keeps every file under its own directory, so ``ddjvu`` finds
        the pages beside the index when the scans are cut.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        files = write_djvu_indirect(fx_samples, pages=DJVU_PAGES, index_name=DJVU_INDEX_NAME)
        uploads = [pdf_upload(fx_samples, PDF_AFTER_DJVU_NAME), *djvu_uploads(files[::-1])]

        job = await _import(fx_rig, fx_owner, fx_book.id, uploads)

        pdf, djvu = await _sources(fx_rig, fx_book.id)
        scans = [scan for scan in await _scans(fx_rig, fx_book.id) if scan.source_id == djvu.id]
        async with fx_rig.sources.source_files(fx_book.id, djvu.id) as stored:
            stored_names = sorted(path.name for path in stored)
        expect(job.state is JobState.SUCCEEDED)
        expect((djvu.kind, djvu.file_name, pdf.file_name) == (SourceKind.DJVU, DJVU_INDEX_NAME, PDF_AFTER_DJVU_NAME))
        expect([file.name for file in djvu.files] == [path.name for path in files])
        expect(djvu.size_bytes == sum(path.stat().st_size for path in files))
        expect(stored_names == sorted(path.name for path in files))
        expect((djvu.scan_count, djvu.metadata[FactKey.DJVU_KIND]) == (len(DJVU_PAGES), DjvuDocumentKind.INDIRECT))
        expect([scan.facts.width_px for scan in sorted(scans, key=attrgetter('number'))] == DJVU_WIDTHS)
        expect(all(scan.renditions.ready for scan in scans))
        assert_expectations()

    async def test_makes_a_source_of_every_bundled_part_and_orders_the_pages_by_the_order_of_the_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify two bundled parts are two sources, whose pages follow the upload and not the names of the parts.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        second = write_djvu_bundle(fx_samples / DJVU_PART_TWO_NAME, pages=DJVU_PAGES[:2])
        tenth = write_djvu_bundle(fx_samples / DJVU_PART_TEN_NAME, pages=DJVU_PAGES[1:])

        await _import(fx_rig, fx_owner, fx_book.id, djvu_uploads([tenth, second]))

        sources, scans, pages = (
            await _sources(fx_rig, fx_book.id),
            await _scans(fx_rig, fx_book.id),
            await _pages(fx_rig, fx_book.id),
        )
        widths = {scan.id: scan.facts.width_px for scan in scans}
        expect([source.file_name for source in sources] == [DJVU_PART_TEN_NAME, DJVU_PART_TWO_NAME])
        expect([source.scan_count for source in sources] == [2, 2])
        expect([widths[page.scan_id] for page in pages if page.scan_id] == [*DJVU_WIDTHS[1:], *DJVU_WIDTHS[:2]])
        assert_expectations()

    async def test_makes_a_source_of_every_single_page_file(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify every single-page file is a source of its own with one scan.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        files = write_djvu_pages(fx_samples, pages=DJVU_PAGES)

        await _import(fx_rig, fx_owner, fx_book.id, djvu_uploads(files))

        sources = await _sources(fx_rig, fx_book.id)
        expect([source.file_name for source in sources] == [path.name for path in files])
        expect([source.scan_count for source in sources] == [1] * len(files))
        assert_expectations()

    async def test_rejects_an_indirect_document_that_lacks_files_and_imports_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify an incomplete indirect document is rejected whole, naming the missing files, and the PDF is imported.

        The page file that is there is not imported alone, and nothing of the rejected document stays staged.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        index, first, *_ = write_djvu_indirect(fx_samples, pages=DJVU_PAGES, index_name=DJVU_INDEX_NAME)

        job = await _import(
            fx_rig,
            fx_owner,
            fx_book.id,
            [*djvu_uploads([index, first]), pdf_upload(fx_samples, PDF_AFTER_DJVU_NAME)],
        )

        [rejected] = await _report(fx_rig, fx_book.id)
        expect(job.state is JobState.SUCCEEDED)
        expect((rejected.file_name, rejected.rejection) == (DJVU_INDEX_NAME, RejectionReason.UNREADABLE))
        expect('p0002.djvu, p0003.djvu' in rejected.detail)
        expect([source.file_name for source in await _sources(fx_rig, fx_book.id)] == [PDF_AFTER_DJVU_NAME])
        assert_expectations()


class TestRunImportChecks:
    """Tests for the checks ImportService.run_import() puts each file through, and what a source gives the book."""

    async def test_stores_a_broken_file_and_a_duplicate_as_rejected_rows_with_their_reasons(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify an upload of a broken file and a second copy of a file gives two rejected rows, and the rest imports.

        The copy is rejected under another name with the name of the source it repeats, and the broken file with the
        message of the reader. Neither is a source of the book, and both are listed with the report of the import.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        content = pdf_upload(fx_samples, LABELLED_PDF_NAME).file.read()

        job = await _import(
            fx_rig,
            fx_owner,
            fx_book.id,
            [
                upload(ORIGINAL_PDF_NAME, content=content),
                upload(COPY_PDF_NAME, content=content),
                upload(UNREADABLE_PDF_NAME, content=BROKEN_PDF),
                image_upload(fx_samples, OTHER_IMAGE_NAME),
            ],
        )

        duplicate, unreadable = await _report(fx_rig, fx_book.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(
            [source.file_name for source in await _sources(fx_rig, fx_book.id)] == [ORIGINAL_PDF_NAME, OTHER_IMAGE_NAME]
        )
        expect((duplicate.file_name, duplicate.status) == (COPY_PDF_NAME, SourceStatus.REJECTED))
        expect(duplicate.rejection is RejectionReason.DUPLICATE)
        expect(ORIGINAL_PDF_NAME in duplicate.detail)
        expect((unreadable.file_name, unreadable.status) == (UNREADABLE_PDF_NAME, SourceStatus.REJECTED))
        expect(unreadable.rejection is RejectionReason.UNREADABLE)
        expect(unreadable.detail != '')
        expect((duplicate.scan_count, unreadable.scan_count) == (0, 0))
        assert_expectations()

    async def test_rejects_a_file_of_an_earlier_upload_and_fails_a_job_that_imports_nothing(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify uploading the same file again imports nothing, so the job fails with the reason of each rejection.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        content = image_upload(fx_samples, COVER_NAME).file.read()
        await _import(fx_rig, fx_owner, fx_book.id, [upload(COVER_NAME, content=content)])

        job = await _import(fx_rig, fx_owner, fx_book.id, [upload(REPEATED_IMAGE_NAME, content=content)])

        expect((job.state, job.error) == (JobState.FAILED, NO_SOURCE_IMPORTED))
        expect(
            [(file.file_name, file.rejection) for file in await _report(fx_rig, fx_book.id)]
            == [(REPEATED_IMAGE_NAME, RejectionReason.DUPLICATE)]
        )
        expect(len(await _sources(fx_rig, fx_book.id)) == 1)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, BAD_FILES, ids=BAD_FILE_IDS)
    async def test_rejects_a_file_no_source_can_be_made_of_and_imports_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, case: BadFile
    ) -> None:
        """Verify one bad file is rejected with its reason and text while the good file of the upload is imported.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param case: A file no source can be made of, with the reason it is rejected for.
        :type case: BadFile
        """
        job = await _import(
            fx_rig,
            fx_owner,
            fx_book.id,
            [upload(case.name, content=case.content), image_upload(fx_samples, PAGE_JPEG_NAME)],
        )

        [rejected] = await _report(fx_rig, fx_book.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(
            (rejected.file_name, rejected.status, rejected.rejection) == (case.name, SourceStatus.REJECTED, case.reason)
        )
        expect(rejected.detail != '')
        expect([source.file_name for source in await _sources(fx_rig, fx_book.id)] == [PAGE_JPEG_NAME])
        assert_expectations()

    async def test_job_that_imports_nothing_fails_and_keeps_every_rejection(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord
    ) -> None:
        """Verify an upload of only bad files fails the job with a message, and keeps no source or page.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        """
        job = await _import(
            fx_rig,
            fx_owner,
            fx_book.id,
            [upload(UNREADABLE_PDF_NAME, content=BROKEN_PDF), upload(TEXT_NAME, content=BROKEN_PDF)],
        )

        expect((job.state, job.error) == (JobState.FAILED, NO_SOURCE_IMPORTED))
        expect(sorted(file.file_name for file in await _report(fx_rig, fx_book.id)) == [UNREADABLE_PDF_NAME, TEXT_NAME])
        expect(await _sources(fx_rig, fx_book.id) == [])
        expect(await _pages(fx_rig, fx_book.id) == [])
        assert_expectations()

    async def test_fills_empty_fields_of_the_description_from_the_first_source_that_has_them(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_samples: Path,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a source fills the empty author, a later source does not replace it, and the title stays.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        fx_book_changes.published.clear()
        await _import(
            fx_rig,
            fx_owner,
            fx_book.id,
            [
                pdf_upload(fx_samples, PDF_NAME, pages=1, metadata={'title': 'Other title', AUTHOR_KEY: FIRST_AUTHOR}),
                pdf_upload(fx_samples, SECOND_PDF_NAME, pages=1, width_px=210, metadata={AUTHOR_KEY: 'Second Author'}),
            ],
        )

        book_record = await _book_record(fx_rig, fx_book.id)
        expect(book_record.details.title == fx_book.details.title)
        expect(book_record.details.contributors == (Contributor(name=FIRST_AUTHOR, role=ContributorRole.AUTHOR),))
        expect([BookResource.RECORD in parts for parts in fx_book_changes.changed_parts()].count(True) == 1)
        assert_expectations()

    async def test_fills_the_lists_of_the_description_from_the_xmp_of_a_source(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_samples: Path,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify the languages, identifiers and subjects of an XMP packet reach an empty description, in one change.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        packet = xmp_packet(
            '<dc:language><rdf:Bag><rdf:li>be</rdf:li></rdf:Bag></dc:language>'
            '<dc:identifier>urn:isbn:0-306-40615-2</dc:identifier>'
            '<dc:subject><rdf:Bag><rdf:li>Folklore</rdf:li></rdf:Bag></dc:subject>'
        )

        path = add_xmp(write_pdf(fx_samples / PDF_NAME, pages=[PdfPage()]), packet=packet)

        fx_book_changes.published.clear()
        await _import(fx_rig, fx_owner, fx_book.id, [upload(PDF_NAME, content=path.read_bytes())])

        details = (await _book_record(fx_rig, fx_book.id)).details
        expect((details.languages, details.subjects) == (('bel',), ('Folklore',)))
        expect([item.value for item in details.identifiers] == ['0306406152'])
        expect([BookResource.RECORD in parts for parts in fx_book_changes.changed_parts()].count(True) == 1)
        assert_expectations()

    async def test_leaves_a_described_book_alone(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path, fx_book_changes: RecordingEventBus
    ) -> None:
        """Verify a field the owner filled is never replaced, and no change is announced when nothing was filled.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book_record = make_record()
        owner_author = (Contributor(name='Owner Author', role=ContributorRole.AUTHOR),)
        described = evolve(book_record, details=evolve(book_record.details, contributors=owner_author))
        await commit_record(fx_rig.database, fx_rig.open_uow(), described, owner=fx_owner.account_id)
        fx_book_changes.published.clear()

        await _import(
            fx_rig,
            fx_owner,
            book_record.id,
            [pdf_upload(fx_samples, PDF_NAME, pages=1, metadata={AUTHOR_KEY: 'Document Author'})],
        )

        expect((await _book_record(fx_rig, book_record.id)).details == described.details)
        expect(not any(BookResource.RECORD in parts for parts in fx_book_changes.changed_parts()))
        assert_expectations()


class TestRunImportLifecycle:
    """Tests for how ImportService.run_import() cuts, stops, continues and ends a job."""

    async def test_cuts_no_more_scans_at_once_than_the_limit_allows(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify the scans are cut in parallel, never more than ``parallel_scans`` at once, and every one is counted.

        The writes of the files overlap, so the cutters report their scans together, and the progress counts the
        file of the upload and each scan once.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        fx_rig.rasterizer.pause = OVERLAP_SECONDS

        job = await _import(
            _tuned(fx_rig, parallel_scans=PARALLEL_SCANS),
            fx_owner,
            fx_book.id,
            [pdf_upload(fx_samples, PDF_NAME, pages=LONG_BOOK_PAGES)],
        )

        scans = await _scans(fx_rig, fx_book.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(fx_rig.rasterizer.peak == PARALLEL_SCANS)
        expect(sorted(fx_rig.rasterizer.extracted) == list(range(LONG_BOOK_PAGES)))
        expect(len(scans) == LONG_BOOK_PAGES and all(scan.renditions.ready for scan in scans))
        expect((job.progress.done, job.progress.total) == (LONG_BOOK_PAGES + 1, LONG_BOOK_PAGES + 1))
        assert_expectations()

    async def test_job_cancelled_between_sources_keeps_the_source_it_committed_and_skips_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify a cancelled job stops before the next source, records the files it never reached, and removes them.

        The cancellation arrives while the first source is inspected, which the job learns of when that source is
        stored and reported. The source stays imported, and the file after it becomes a skipped row that holds no scan.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root under the test's temporary directory.
        :type fx_storage_root: Path
        """
        job = await fx_rig.service().start_import(
            fx_owner,
            fx_book.id,
            [pdf_upload(fx_samples, PDF_NAME, pages=1), pdf_upload(fx_samples, SECOND_PDF_NAME, pages=1, width_px=210)],
        )

        async def cancel_at_the_first_source() -> None:
            """Cancel the job when the first source is about to be inspected."""
            fx_rig.inspector.before_inspect = None
            await fx_rig.fakes.board.cancel(job.id)

        fx_rig.inspector.before_inspect = cancel_at_the_first_source

        await fx_rig.fakes.board.run_queued()

        stored = fx_rig.stored_job(job)
        [skipped] = await _report(fx_rig, fx_book.id)
        expect(stored.state is JobState.CANCELLED)
        expect([source.file_name for source in await _sources(fx_rig, fx_book.id)] == [PDF_NAME])
        expect((skipped.file_name, skipped.status, skipped.rejection) == (SECOND_PDF_NAME, SourceStatus.SKIPPED, None))
        expect((skipped.kind, skipped.file_type, skipped.scan_count) == (SourceKind.PDF, FileType.PDF, 0))
        expect(not [path async for path in anyio.Path(fx_storage_root).rglob(INCOMING_UPLOADS)])
        expect(_events_of(fx_rig, JobChanged)[-1].job == stored)
        assert_expectations()

    async def test_cancellation_records_every_file_not_reached_and_is_raised_again(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify a job stopped in the middle of the checks skips the files after it, with a type only when it has one.

        The control tells the job it was cancelled when the first file has been rejected. The file that was rejected
        stays rejected, the others are skipped, and the cancellation goes on to the board, which ends the job with it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root under the test's temporary directory.
        :type fx_storage_root: Path
        """
        job = await fx_rig.service().start_import(
            fx_owner,
            fx_book.id,
            [
                upload(TEXT_NAME, content=BROKEN_PDF),
                pdf_upload(fx_samples, PDF_NAME, pages=1),
                upload(UNKNOWN_TYPE_NAME, content=BROKEN_PDF),
            ],
        )
        # The first report is the count of the scans to cut, the second is the one after the first file was rejected
        control = Mock(spec=JobControl)
        control.report = AsyncMock(side_effect=[None, JobCancelledError])

        with pytest.raises(JobCancelledError):
            await fx_rig.service().run_import(job, control)

        rows = {row.file_name: row for row in await _report(fx_rig, fx_book.id)}
        expect(
            {name: (row.status, row.kind, row.file_type) for name, row in rows.items()}
            == {
                TEXT_NAME: (SourceStatus.REJECTED, None, None),
                PDF_NAME: (SourceStatus.SKIPPED, SourceKind.PDF, FileType.PDF),
                UNKNOWN_TYPE_NAME: (SourceStatus.SKIPPED, None, None),
            }
        )
        expect(rows[TEXT_NAME].rejection is RejectionReason.UNSUPPORTED_TYPE)
        expect(await _sources(fx_rig, fx_book.id) == [])
        expect(not [path async for path in anyio.Path(fx_storage_root).rglob(INCOMING_UPLOADS)])
        assert_expectations()

    async def test_job_cancelled_between_scans_stops_before_the_next_scan_and_the_next_job_cuts_the_rest(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_samples: Path,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify scans left without renditions by a cancelled job are cut first by the book's next import.

        The cancelled job keeps its source and pages. The next job writes the directories the cancelled one left
        half written, since a stored file is never replaced, and counts those scans in its own progress.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        one_at_a_time = _tuned(fx_rig, parallel_scans=1)
        job = await one_at_a_time.service().start_import(
            fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=THREE_PAGES)]
        )

        async def cancel_after_the_first_scan(event: DomainEvent) -> None:
            """Cancel the job once, as the first scan is announced and the second is not started.

            :param event: Event just delivered.
            :type event: DomainEvent
            """
            if _cuts_a_scan(event):
                fx_book_changes.after_publish = None
                await one_at_a_time.fakes.board.cancel(job.id)

        fx_book_changes.after_publish = cancel_after_the_first_scan

        await one_at_a_time.fakes.board.run_queued()

        cancelled = one_at_a_time.stored_job(job)
        expect(cancelled.state is JobState.CANCELLED)
        # The second scan is never started, since the job is found cancelled before it
        expect(fx_rig.rasterizer.extracted == [0])
        expect([scan.renditions.ready for scan in await _scans(fx_rig, fx_book.id)] == [True, False, False])
        expect(len(await _pages(fx_rig, fx_book.id)) == THREE_PAGES)

        next_job = await _import(one_at_a_time, fx_owner, fx_book.id, [image_upload(fx_samples, COVER_NAME)])

        expect(next_job.state is JobState.SUCCEEDED)
        expect(all(scan.renditions.ready for scan in await _scans(fx_rig, fx_book.id)))
        # The file of the new upload, its scan, and the two scans the cancelled job left are all counted in its progress
        expect((next_job.progress.done, next_job.progress.total) == (THREE_PAGES + 1, THREE_PAGES + 1))
        assert_expectations()

    async def test_import_after_a_crash_keeps_what_was_stored_and_rejects_it_as_a_duplicate(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify a restarted process keeps the source a crashed import stored, and the same upload cuts the rest.

        The worker dies while it cuts the second scan, so the board never ends the job and nothing resumes it. The
        start of the process removes what the job staged, and a board with no jobs takes the same file uploaded again.
        The source and the scans of the crashed import stay, so the file is rejected as a duplicate, and the new file
        of the upload is imported along with the scans the crash left without images.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root under the test's temporary directory.
        :type fx_storage_root: Path
        """
        content = pdf_upload(fx_samples, PDF_NAME, pages=THREE_PAGES).file.read()
        crashing = _tuned(fx_rig, parallel_scans=1)
        await crashing.service().start_import(fx_owner, fx_book.id, [upload(PDF_NAME, content=content)])
        fx_rig.tiler.crash_on_preview = CRASHING_PREVIEW
        with pytest.raises(BaseExceptionGroup):
            await crashing.fakes.board.run_queued()
        stored = await _sources(fx_rig, fx_book.id)

        await fx_rig.sources.discard_all_staged()
        restarted = _tuned(fx_rig, parallel_scans=1)
        job = await _import(
            restarted,
            fx_owner,
            fx_book.id,
            [upload(PDF_NAME, content=content), image_upload(fx_samples, COVER_NAME)],
        )

        expect(
            [(row.file_name, row.rejection) for row in await _report(fx_rig, fx_book.id)]
            == [(PDF_NAME, RejectionReason.DUPLICATE)]
        )
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in await _sources(fx_rig, fx_book.id)] == [PDF_NAME, COVER_NAME])
        expect((await _sources(fx_rig, fx_book.id))[0] == stored[0])
        expect(len(await _scans(fx_rig, fx_book.id)) == THREE_PAGES + 1)
        expect(all(scan.renditions.ready for scan in await _scans(fx_rig, fx_book.id)))
        expect(not [path async for path in anyio.Path(fx_storage_root).rglob(INCOMING_UPLOADS)])
        assert_expectations()

    async def test_damaged_scan_fails_the_job_with_the_message_of_the_error(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify a scan that cannot be written fails the job with the reason, and the upload is removed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root under the test's temporary directory.
        :type fx_storage_root: Path
        """
        fx_rig.rasterizer.failures[1] = UnsupportedSourceError(DAMAGED_PAGE_ERROR)

        job = await _import(
            _tuned(fx_rig, parallel_scans=1),
            fx_owner,
            fx_book.id,
            [pdf_upload(fx_samples, PDF_NAME, pages=THREE_PAGES)],
        )

        expect((job.state, job.error) == (JobState.FAILED, DAMAGED_PAGE_ERROR))
        expect(len(await _sources(fx_rig, fx_book.id)) == 1)
        expect(not [path async for path in anyio.Path(fx_storage_root).rglob(INCOMING_UPLOADS)])
        assert_expectations()

    async def test_unexpected_error_fails_the_job_without_leaking_its_message(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify an error nobody foresaw ends the job as failed with a fixed message, so the book is not stuck.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        fx_rig.rasterizer.failures[0] = RuntimeError(SECRET_FAULT)

        job = await _import(fx_rig, fx_owner, fx_book.id, [image_upload(fx_samples, IMAGE_NAME)])

        expect((job.state, job.error) == (JobState.FAILED, JobEnd.UNEXPECTED_FAILURE))
        expect(fx_rig.fakes.board.jobs(fx_book.id, active=True) == [])
        assert_expectations()


class TestRunImportFailures:
    """Tests for what ImportService.run_import() leaves behind when a step fails."""

    async def test_source_whose_files_cannot_be_promoted_is_withdrawn_whole(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a failed promotion leaves no source without files: no source, scan or page, and no counted scans.

        The source was committed before its files moved, so the run takes it back with its pages, which a bare delete
        of the source would leave behind without a scan.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        fx_rig.sources.fail_on_promote = OSError('disk full')

        job = await _import(fx_rig, fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=THREE_PAGES)])

        expect(job.state is JobState.FAILED)
        expect(job.error == JobEnd.UNEXPECTED_FAILURE)
        expect(await _sources(fx_rig, fx_book.id) == [])
        expect(await _scans(fx_rig, fx_book.id) == [])
        expect(await _pages(fx_rig, fx_book.id) == [])
        expect((job.progress.done, job.progress.total) == (0, 1))
        assert_expectations()

    async def test_failed_job_counts_only_the_scans_that_were_committed(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a step that fails before its commit leaves its scans out of the progress of the failed job.

        The progress counts the one file of the upload, and none of its scans.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        failure = UnsupportedSourceError(REFUSED_SCANS_ERROR)
        with patch.object(SqlAlchemyScanRepository, 'add_many', Mock(side_effect=failure)):
            job = await _import(fx_rig, fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=THREE_PAGES)])

        expect((job.state, job.error) == (JobState.FAILED, REFUSED_SCANS_ERROR))
        expect((job.progress.done, job.progress.total) == (0, 1))
        expect(await _sources(fx_rig, fx_book.id) == [])
        assert_expectations()


class TestRunImportQueue:
    """Tests for the scans cut at once, whose changes of the book queue in front of one unit of work of the job."""

    async def test_job_cancelled_while_scans_are_cut_stops_every_cutter_and_keeps_the_scans_written(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_samples: Path,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a cancellation stops both cutters and the next scans, and leaves the scan that was stored stored.

        The second scan of the first pair waits outside any transaction until the job is cancelled. The cutter of the
        first scan finds the job cancelled when it reports its scan, which stops its group and so the cutter of the
        second scan before it stores anything, while the first scan was committed before. The third scan is never
        started.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        job = await fx_rig.service().start_import(
            fx_owner, fx_book.id, [pdf_upload(fx_samples, QUEUE_PDF, pages=FOUR_PAGES)]
        )
        cancelled = anyio.Event()

        async def hold_second_scan(number: int) -> None:
            """Keep the second scan outside its transactions until the job has been cancelled.

            :param number: Number of the scan about to be written.
            :type number: int
            """
            if number == 1:
                await cancelled.wait()

        async def cancel_after_the_first_scan(event: DomainEvent) -> None:
            """Cancel the job once, as the first scan is announced, and let the second scan go on.

            :param event: Event just delivered.
            :type event: DomainEvent
            """
            if _cuts_a_scan(event):
                fx_book_changes.after_publish = None
                await fx_rig.fakes.board.cancel(job.id)
                cancelled.set()

        fx_rig.rasterizer.before_extract = hold_second_scan
        fx_book_changes.after_publish = cancel_after_the_first_scan

        with anyio.fail_after(IMPORT_TIMEOUT_SECONDS):
            await fx_rig.fakes.board.run_queued()

        stored = fx_rig.stored_job(job)
        expect(stored.state is JobState.CANCELLED)
        expect(sorted(fx_rig.rasterizer.extracted) == [0, 1])
        expect(fx_rig.rasterizer.running == 0)
        expect([scan.renditions.ready for scan in await _scans(fx_rig, fx_book.id)] == [True] + [False] * 3)
        expect(len(await _pages(fx_rig, fx_book.id)) == FOUR_PAGES)
        assert_expectations()

    async def test_cutters_that_find_the_book_held_wait_for_it_and_count_every_scan(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify two cutters that ask for the held book wait for it together and do not fail, and both scans count.

        Both cutters have written their image when the book is held, so each asks for the change that stores its scan
        and waits for the book. Neither has stored anything meanwhile. When the book is let go they store their scans
        in turn, each counting its own scan on top of the progress the other committed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await fx_rig.service().start_import(
            fx_owner, fx_book.id, [pdf_upload(fx_samples, QUEUE_PDF, pages=PARALLEL_SCANS)]
        )
        extracting: list[int] = []
        both_extracting = anyio.Event()
        proceed = anyio.Event()

        async def wait_for_both(number: int) -> None:
            """Keep each scan before its write until both cutters have come, and the test lets them go.

            :param number: Number of the scan about to be written.
            :type number: int
            """
            extracting.append(number)
            if len(extracting) == PARALLEL_SCANS:
                both_extracting.set()
            await proceed.wait()

        fx_rig.rasterizer.before_extract = wait_for_both

        with anyio.fail_after(IMPORT_TIMEOUT_SECONDS):
            async with anyio.create_task_group() as group:
                group.start_soon(fx_rig.fakes.board.run_queued)
                await both_extracting.wait()
                async with hold_book_in_thread(fx_rig.open_uow(), fx_book.id):
                    proceed.set()
                    await anyio.wait_all_tasks_blocked()
                    expect(not any(scan.renditions.ready for scan in await _scans(fx_rig, fx_book.id)))

        stored = fx_rig.stored_job(job)
        expect((stored.state, stored.error) == (JobState.SUCCEEDED, ''))
        expect(fx_rig.rasterizer.peak == PARALLEL_SCANS)
        expect(all(scan.renditions.ready for scan in await _scans(fx_rig, fx_book.id)))
        expect((stored.progress.done, stored.progress.total) == (1 + PARALLEL_SCANS, 1 + PARALLEL_SCANS))
        assert_expectations()

    async def test_fault_raised_while_a_cutter_stops_on_a_cancellation_is_reported_as_the_fault(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_samples: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify a fault met while a cutter unwinds from the cancellation of its group fails the job, and is logged.

        The first cutter reports that the job was cancelled, which cancels its group and so the second cutter, whose
        cleanup then fails. The group holds the cancellation and the fault, and the cancellation must not hide it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param caplog: Fixture capturing what the service logs.
        :type caplog: pytest.LogCaptureFixture
        """
        arrivals: list[Scan] = []
        second_arrived = anyio.Event()

        async def fail_while_stopping(_run: ImportRun, _kind: SourceKind, scan: Scan) -> None:
            """Report a cancellation from the first cutter, and fail the second one as it is cancelled for it.

            :param _run: Run cutting the scan, which the patched step replaces.
            :type _run: ImportRun
            :param _kind: Kind of the source holding the scan.
            :type _kind: SourceKind
            :param scan: The scan to cut.
            :type scan: Scan
            :raises JobCancelledError: For the scan that arrives first, once the second has arrived.
            :raises RuntimeError: For the scan that arrives second, when the group cancels it.
            """
            arrivals.append(scan)
            if len(arrivals) < PARALLEL_SCANS:
                await second_arrived.wait()
                raise JobCancelledError
            second_arrived.set()
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                # A cleanup that fails in place of the cancellation, which is what an asset store that cannot remove
                # the half written files of the scan does
                raise RuntimeError(SECRET_FAULT) from None

        with patch.object(ImportRun, CUT_STEP, fail_while_stopping):
            job = await _import(fx_rig, fx_owner, fx_book.id, [pdf_upload(fx_samples, QUEUE_PDF, pages=PARALLEL_SCANS)])

        expect((job.state, job.error) == (JobState.FAILED, JobEnd.UNEXPECTED_FAILURE))
        expect(SECRET_FAULT in caplog.text)
        assert_expectations()


class TestRunImportSplit:
    """Tests for the run of the page split that an import queues on the pages it made."""

    @staticmethod
    def _recipes(processor_key: str) -> DefaultRecipes:
        """Give the recipes a stage starts with, the page split being the given processor.

        :param processor_key: Key of the processor of the one step of the page split recipe.
        :type processor_key: str
        :returns: The recipes.
        :rtype: DefaultRecipes
        """
        return DefaultRecipes(
            {Stage.PAGE_SPLIT: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(processor_key,)))}
        )

    @staticmethod
    def _offering(rig: ImportRig, processor_key: str) -> ImportRig:
        """Give the rig a board whose service has ``split.none`` and ``split.auto``, and the page split as given.

        :param rig: Adapters of the import.
        :type rig: ImportRig
        :param processor_key: Key of the processor of the one step of the page split recipe.
        :type processor_key: str
        :returns: The rig whose board runs the service.
        :rtype: ImportRig
        """
        return _tuned(
            rig,
            defaults=TestRunImportSplit._recipes(processor_key),
            processors=[SplitNone(), AutoSplitProcessor()],
        )

    @staticmethod
    def _split_jobs(rig: ImportRig) -> list[Job]:
        """List the runs of a stage that were put on the board of the rig, in the order they were put.

        :param rig: Adapters of the import.
        :type rig: ImportRig
        :returns: The jobs.
        :rtype: list[Job]
        """
        return [job for job in rig.fakes.board.put_jobs if job.kind is JobKind.RUN_STAGE]

    async def test_queues_a_split_of_the_new_pages_when_the_recipe_is_automatic(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify the finished import queues one run of the page split naming the pages of the sources it imported.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        offering = self._offering(fx_rig, AUTO_SPLIT)

        job = await _import(offering, fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=2)])

        [queued] = self._split_jobs(offering)
        pages = await _pages(fx_rig, fx_book.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(
            StageRun.from_map(queued.params) == StageRun(stage=Stage.PAGE_SPLIT, page_ids=tuple(p.id for p in pages))
        )
        expect(queued.book_id == fx_book.id)
        assert_expectations()

    async def test_a_later_import_queues_only_its_own_pages(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify the pages of an earlier import, which the user may have changed, are left out of the next run.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        offering = self._offering(fx_rig, AUTO_SPLIT)
        await _import(offering, fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=2)])
        await _import(offering, fx_owner, fx_book.id, [pdf_upload(fx_samples, SECOND_PDF_NAME, pages=1, width_px=210)])

        runs = [StageRun.from_map(job.params) for job in self._split_jobs(offering)]
        pages = await _pages(fx_rig, fx_book.id)
        assert [run.page_ids for run in runs][1] == (pages[-1].id,)

    async def test_queues_nothing_when_the_recipe_was_chosen_by_hand(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a book whose page split recipe is not the automatic one is not split by the import.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        offering = self._offering(fx_rig, 'split.none')

        job = await _import(offering, fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=2)])

        expect(job.state is JobState.SUCCEEDED)
        expect(self._split_jobs(offering) == [])
        assert_expectations()

    async def test_a_split_that_cannot_be_queued_does_not_fail_the_import(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path
    ) -> None:
        """Verify a book that is processing something already still ends its import as succeeded.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        offering = self._offering(fx_rig, AUTO_SPLIT)
        job = await offering.service().start_import(fx_owner, fx_book.id, [pdf_upload(fx_samples, PDF_NAME, pages=1)])
        # A run queued behind the import, which is still queued when the import ends and asks for its own
        busy = offering.fakes.board.put(make_job(book_id=fx_book.id, kind=JobKind.RUN_STAGE))

        await offering.fakes.board.run_queued()

        expect(offering.stored_job(job).state is JobState.SUCCEEDED)
        expect(self._split_jobs(offering) == [busy])
        assert_expectations()


async def delete_the_page(rig: ImportRig, book_record: BookRecord) -> None:
    """Delete the only page of the book in a change of its own, as the user would while its scan is cut.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_record: Record of the book whose page is deleted.
    :type book_record: BookRecord
    """
    [page] = await _pages(rig, book_record.id)
    await rig.open_uow().change_book(book_record.id, lambda repos, _book_record: repos.pages.delete(page.id))


async def delete_the_source(rig: ImportRig, book_record: BookRecord) -> None:
    """Delete the only source of the book, with its scan, in a change of its own.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_record: Record of the book whose source is deleted.
    :type book_record: BookRecord
    """
    [source] = await _sources(rig, book_record.id)
    await rig.open_uow().change_book(book_record.id, lambda repos, _book_record: repos.sources.delete(source.id))


async def make_the_scan_ready(rig: ImportRig, book_record: BookRecord) -> None:
    """Mark the only scan of the book ready in a change of its own, as another run would have.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param book_record: Record of the book whose scan is marked.
    :type book_record: BookRecord
    """
    [scan] = await _scans(rig, book_record.id)
    ready = evolve(scan, renditions=evolve(scan.renditions, ready=True))
    await rig.open_uow().change_book(book_record.id, lambda repos, _book_record: repos.scans.update(ready))


class TestRunImportApplyStep:
    """Tests for the change that stores a cut scan, which reads the scan and its pages again."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            pytest.param(delete_the_page, id='page-deleted'),
            pytest.param(delete_the_source, id='source-deleted'),
            pytest.param(make_the_scan_ready, id='scan-ready-already'),
        ],
    )
    async def test_scan_that_changed_while_its_images_were_cut_is_not_stored(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_samples: Path,
        case: Callable[[ImportRig, BookRecord], Awaitable[None]],
    ) -> None:
        """Verify a scan whose book changed after the cut writes no version and announces nothing, and the job ends well.

        The change is made right before the image of the scan is written, so the change that stores the scan finds
        the book as the other one left it. The scan counts one less to cut, and its files are removed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param case: What another request does to the book while the scan is cut.
        :type case: Callable[[ImportRig, BookRecord], Awaitable[None]]
        """
        cut: list[Scan] = []

        async def change_the_book_once(_number: int) -> None:
            """Change the book, the first time a scan is about to be written.

            :param _number: Number of the scan in its source.
            :type _number: int
            """
            fx_rig.rasterizer.before_extract = None
            cut.extend(await _scans(fx_rig, fx_book.id))
            await case(fx_rig, fx_book)

        fx_rig.rasterizer.before_extract = change_the_book_once

        job = await _import(fx_rig, fx_owner, fx_book.id, [image_upload(fx_samples, IMAGE_NAME)])

        pages = await _pages(fx_rig, fx_book.id)
        versions = await fx_rig.open_uow().read_book(
            fx_book.id, lambda repos: repos.page_versions.list_base_versions([page.id for page in pages])
        )
        # The progress counts the file of the upload, and the scan counted one less to cut
        expect((job.state, job.progress.done, job.progress.total) == (JobState.SUCCEEDED, 1, 1))
        expect(not versions)
        expect(not await _is_stored(fx_rig.assets, BookKeys(fx_book.id).scan_directory(cut[0])))
        assert_expectations()


class TestStartImportConcurrently:
    """Tests for two uploads of one book that meet at the check of the active import."""

    async def test_two_uploads_that_pass_the_early_check_together_put_one_job_and_remove_the_other_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_book: BookRecord, fx_samples: Path, fx_storage_root: Path
    ) -> None:
        """Verify of two uploads that both passed the early check, one puts its job and the other is refused.

        Both uploads pass the check of the book and receive their files before either puts its job, since the
        store holds each of them until the other has received its files. Then the check is made again with no
        ``await`` before the put, so the first to run puts its job and the second finds it, and the files the second
        received are removed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param fx_storage_root: Storage root under the test's temporary directory.
        :type fx_storage_root: Path
        """
        received = 0
        both_received = anyio.Event()
        outcomes: dict[str, Job | ConflictError] = {}
        stage = fx_rig.sources.stage

        async def stage_and_wait(
            book_id: BookId, job_id: JobId, files: Sequence[IncomingFile], *, max_bytes: int
        ) -> Sequence[SourceFile]:
            """Receive the files of an upload, then wait until the files of both uploads have been received.

            :param book_id: Book receiving the upload.
            :type book_id: BookId
            :param job_id: Import job owning the upload.
            :type job_id: JobId
            :param files: Uploaded files.
            :type files: Sequence[IncomingFile]
            :param max_bytes: Largest total size of the upload in bytes.
            :type max_bytes: int
            :returns: Name, size and digest of every staged file.
            :rtype: Sequence[SourceFile]
            """
            nonlocal received
            staged = await stage(book_id, job_id, files, max_bytes=max_bytes)
            received += 1
            if received == len(RACING_UPLOADS):
                both_received.set()
            await both_received.wait()
            return staged

        async def upload_file(name: str) -> None:
            """Upload one file through a service of its own and keep the job, or the refusal.

            :param name: Name of the uploaded file.
            :type name: str
            """
            try:
                outcomes[name] = await fx_rig.service().start_import(
                    fx_owner, fx_book.id, [image_upload(fx_samples, name)]
                )
            except ConflictError as error:
                outcomes[name] = error

        with (
            patch.object(fx_rig.sources, STAGE_METHOD, stage_and_wait),
            anyio.fail_after(IMPORT_TIMEOUT_SECONDS),
        ):
            async with anyio.create_task_group() as group:
                for name in RACING_UPLOADS:
                    group.start_soon(upload_file, name)

        jobs = [outcome for outcome in outcomes.values() if not isinstance(outcome, ConflictError)]
        refusals = [outcome for outcome in outcomes.values() if isinstance(outcome, ConflictError)]
        expect(received == len(RACING_UPLOADS))
        expect(fx_rig.fakes.board.put_jobs == jobs and len(jobs) == 1)
        expect([str(refusal) for refusal in refusals] == [IMPORT_ACTIVE])
        expect(len([path async for path in anyio.Path(fx_storage_root).rglob(INCOMING_UPLOADS)]) == 1)
        assert_expectations()


@pytest.fixture
def fx_sources(fx_rig: ImportRig) -> SourceService:
    """Build the service of the sources over the database, the stores and the board of the rig.

    :param fx_rig: Adapters of the import.
    :type fx_rig: ImportRig
    :returns: The source service of a new request.
    :rtype: SourceService
    """
    uow = fx_rig.open_uow()
    return SourceService(
        uow=uow, library=Library(uow=uow), board=fx_rig.fakes.board, sources=fx_rig.sources, assets=fx_rig.assets
    )


@pytest.fixture
async def fx_files(fx_rig: ImportRig, fx_book: BookRecord) -> list[Source]:
    """Store an imported, a rejected and a skipped file in ``fx_project``, recorded in that order.

    :param fx_rig: Adapters of the import.
    :type fx_rig: ImportRig
    :param fx_book: Book of the owner.
    :type fx_book: BookRecord
    :returns: The imported source, the rejected file and the skipped file.
    :rtype: list[Source]
    """
    imported = make_source(name=PDF_NAME, minutes=0)
    rejected = evolve(
        make_source(name=TEXT_NAME, minutes=1),
        kind=None,
        file_type=None,
        scan_count=0,
        status=SourceStatus.REJECTED,
        rejection=RejectionReason.UNSUPPORTED_TYPE,
        detail=RejectionReason.UNSUPPORTED_TYPE.label,
    )
    skipped = evolve(
        make_source(name=SECOND_PDF_NAME, minutes=2),
        scan_count=0,
        status=SourceStatus.SKIPPED,
    )
    files = [imported, rejected, skipped]
    await fx_rig.open_uow().change_book(fx_book.id, lambda repos, _book_record: repos.sources.add_many(files))
    return files


class StatusCase(NamedTuple):
    """The statuses a listing asks for, and which of the stored files it must return.

    :ivar statuses: Statuses asked for.
    :ivar positions: Positions of the returned files among the imported, the rejected and the skipped file.
    """

    statuses: tuple[SourceStatus, ...]
    positions: tuple[int, ...]


class TestSourceReport:
    """Tests for the files of an import that SourceService lists and closes: the rejected and the skipped ones."""

    async def test_lists_only_the_imported_sources_unless_told_otherwise(
        self, fx_sources: SourceService, fx_owner: Actor, fx_book: BookRecord, fx_files: list[Source]
    ) -> None:
        """Verify a listing with no statuses is the list of the sources of the book.

        :param fx_sources: Service of the sources.
        :type fx_sources: SourceService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_files: An imported, a rejected and a skipped file of the book.
        :type fx_files: list[Source]
        """
        listed = await fx_sources.list(fx_owner, fx_book.id, BOOK_WINDOW)

        expect(list(listed.items) == fx_files[:1])
        expect(listed.total == 1)
        assert_expectations()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            StatusCase(statuses=(SourceStatus.REJECTED,), positions=(1,)),
            StatusCase(statuses=(SourceStatus.REJECTED, SourceStatus.SKIPPED), positions=(1, 2)),
            StatusCase(statuses=tuple(SourceStatus), positions=(0, 1, 2)),
        ],
        ids=['rejected', 'report', 'every-status'],
    )
    async def test_lists_the_files_of_the_statuses_asked_for_in_the_order_they_were_recorded(
        self,
        fx_sources: SourceService,
        fx_owner: Actor,
        fx_book: BookRecord,
        fx_files: list[Source],
        case: StatusCase,
    ) -> None:
        """Verify a listing returns the files in the given statuses only, and counts only them.

        :param fx_sources: Service of the sources.
        :type fx_sources: SourceService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_files: An imported, a rejected and a skipped file of the book.
        :type fx_files: list[Source]
        :param case: The statuses asked for, and the files they must return.
        :type case: StatusCase
        """
        listed = await fx_sources.list(fx_owner, fx_book.id, BOOK_WINDOW, statuses=case.statuses)

        expect(list(listed.items) == [fx_files[position] for position in case.positions])
        expect(listed.total == len(case.positions))
        assert_expectations()

    async def test_deleting_the_report_removes_the_rejected_and_skipped_files_and_keeps_the_sources(
        self, fx_rig: ImportRig, fx_sources: SourceService, fx_owner: Actor, fx_book: BookRecord, fx_files: list[Source]
    ) -> None:
        """Verify closing the report of an import deletes its rows and no imported source.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_sources: Service of the sources.
        :type fx_sources: SourceService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_files: An imported, a rejected and a skipped file of the book.
        :type fx_files: list[Source]
        """
        await fx_sources.delete_unimported(fx_owner, fx_book.id)

        expect(await _report(fx_rig, fx_book.id) == [])
        expect(await _sources(fx_rig, fx_book.id) == fx_files[:1])
        assert_expectations()

    async def test_deleting_the_report_while_the_book_imports_is_a_conflict(
        self, fx_rig: ImportRig, fx_sources: SourceService, fx_owner: Actor, fx_book: BookRecord, fx_files: list[Source]
    ) -> None:
        """Verify the report is kept while an import is queued or running, since that import is still writing it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_sources: Service of the sources.
        :type fx_sources: SourceService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookRecord
        :param fx_files: An imported, a rejected and a skipped file of the book.
        :type fx_files: list[Source]
        """
        fx_rig.fakes.board.put(make_job(book_id=fx_book.id))

        with pytest.raises(ConflictError, match=SOURCE_IMPORTING):
            await fx_sources.delete_unimported(fx_owner, fx_book.id)

        assert await _report(fx_rig, fx_book.id) == fx_files[1:]

    async def test_deleting_the_report_of_a_book_of_another_account_is_not_found_and_keeps_it(
        self, fx_rig: ImportRig, fx_sources: SourceService, fx_book: BookRecord, fx_files: list[Source]
    ) -> None:
        """Verify an account cannot close, or learn about, the report of a book it does not own.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_sources: Service of the sources.
        :type fx_sources: SourceService
        :param fx_book: Book of another account.
        :type fx_book: BookRecord
        :param fx_files: An imported, a rejected and a skipped file of the book.
        :type fx_files: list[Source]
        """
        with pytest.raises(NotFoundError):
            await fx_sources.delete_unimported(Actor(account_id=new_account_id()), fx_book.id)

        assert await _report(fx_rig, fx_book.id) == fx_files[1:]
