"""Tests for the import service: receiving an upload, and running the job it becomes, source by source."""

import asyncio
from functools import partial
from operator import attrgetter, itemgetter
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.imaging.common import FactKey
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.memory.unit_of_work import InMemoryJobRepository, InMemoryScanRepository
from bookreviver.domain.entities import Actor, VersionInputs
from bookreviver.domain.enums import (
    ColorMode,
    ContributorRole,
    DjvuDocumentKind,
    ImagePolicy,
    JobKind,
    JobState,
    LabelStyle,
    NumberDisplay,
    PageChange,
    PageOrigin,
    RecipeKind,
    RejectionReason,
    Rendition,
    SourceKind,
    Stage,
    UploadProblem,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError, UnsupportedSourceError, UploadRejectedError
from bookreviver.domain.events import (
    DomainEvent,
    JobChanged,
    PagesChanged,
    ProjectChanged,
    ScanReady,
    SourceImported,
)
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Contributor, SliceRequest, StageRun
from bookreviver.plugins.split_none import SplitNone
from bookreviver.services.base_versions import SPLIT_NONE
from bookreviver.services.imports import (
    IMPORT_ACTIVE,
    NO_SOURCE_IMPORTED,
    NOT_QUEUED,
    UNEXPECTED_FAILURE,
    ImportCancelledError,
    ImportRun,
)
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
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
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_imports import (
    DEFAULT_PARALLEL_SCANS,
    IIIF_ROOT,
    MAX_BYTES,
    MAX_FILES,
    OVERLAP_SECONDS,
    PAGE_WIDTH_PX,
    ImportRig,
    WorkerCrashError,
    djvu_uploads,
    image_upload,
    pdf_upload,
)
from tests.helpers.processors import AutoSplitProcessor
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import UploadFile

    from bookreviver.domain.entities import Job, Page, PaginationSection, Project, Scan, Source
    from bookreviver.domain.ids import JobId, ProjectId, StorageKey
    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

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
EVERY_SLICE: SliceRequest = SliceRequest(limit=1000)
# Pages of the sample PDFs, and the scans they and the one image make in all
FIRST_PART_PAGES: int = 2
SECOND_PART_PAGES: int = 3
SCAN_COUNT: int = FIRST_PART_PAGES + SECOND_PART_PAGES + 1
SECOND_PART_WIDTH_PX: int = 210
SOURCE_COUNT: int = 3
THREE_PAGES: int = 3
LONG_BOOK_PAGES: int = 6
PARALLEL_SCANS: int = 2
SECRET_FAULT: str = 'secret detail of the failure'
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
def fx_rig(tmp_path: Path) -> ImportRig:
    """Build the adapters an import runs on, over a storage root in the test's temporary directory.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: The rig with an empty database and nothing stored.
    :rtype: ImportRig
    """
    return ImportRig.build(tmp_path / 'storage')


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
    """Build the account that owns the project.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


@pytest.fixture
async def fx_project(fx_rig: ImportRig, fx_owner: Actor) -> Project:
    """Store a project of ``fx_owner`` with no sources.

    :param fx_rig: Adapters of the import.
    :type fx_rig: ImportRig
    :param fx_owner: Account owning the project.
    :type fx_owner: Actor
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=fx_owner.account_id)
    await fx_rig.fakes.store(project)
    return project


@pytest.fixture
def fx_book_files(fx_samples: Path) -> list[UploadFile]:
    """Build a book of two PDF parts and a separate cover, uploaded in an order other than the book's.

    :param fx_samples: Directory the sample files are built in.
    :type fx_samples: Path
    :returns: The cover first, then the second part, then the first part.
    :rtype: list[UploadFile]
    """
    return [
        image_upload(fx_samples, 'part3-cover.jpg'),
        pdf_upload(fx_samples, 'part2.pdf', pages=SECOND_PART_PAGES, width_px=SECOND_PART_WIDTH_PX),
        pdf_upload(fx_samples, 'part1.pdf', pages=FIRST_PART_PAGES),
    ]


async def _import(
    rig: ImportRig,
    actor: Actor,
    project_id: ProjectId,
    files: list[UploadFile],
    *,
    parallel_scans: int = DEFAULT_PARALLEL_SCANS,
) -> Job:
    """Upload the files and run the job, each in a service of its own as a request and a worker would.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param actor: Account acting in the request.
    :type actor: Actor
    :param project_id: Project to import into.
    :type project_id: ProjectId
    :param files: The uploaded files.
    :type files: list[UploadFile]
    :param parallel_scans: Largest number of scans the service that runs the job cuts at once.
    :type parallel_scans: int
    :returns: The job as stored after the run.
    :rtype: Job
    """
    job = await rig.service().start_import(actor, project_id, files)
    await rig.service(parallel_scans=parallel_scans).run_import(job.id)
    return await rig.stored_job(job)


async def _sources(rig: ImportRig, project_id: ProjectId) -> list[Source]:
    """Read the sources of a project as committed, in import order.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param project_id: Project owning the sources.
    :type project_id: ProjectId
    :returns: The sources.
    :rtype: list[Source]
    """
    return list(await rig.open_uow().sources.list_for_project(project_id))


async def _scans(rig: ImportRig, project_id: ProjectId) -> list[Scan]:
    """Read the scans of a project as committed, source by source.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param project_id: Project owning the scans.
    :type project_id: ProjectId
    :returns: The scans.
    :rtype: list[Scan]
    """
    return list((await rig.open_uow().scans.list_for_project(project_id, EVERY_SLICE)).items)


async def _pages(rig: ImportRig, project_id: ProjectId) -> list[Page]:
    """Read the pages of a project as committed, in book order.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param project_id: Project owning the pages.
    :type project_id: ProjectId
    :returns: The pages.
    :rtype: list[Page]
    """
    return list((await rig.open_uow().pages.list_for_project(project_id, EVERY_SLICE)).items)


async def _sections(rig: ImportRig, project_id: ProjectId) -> list[PaginationSection]:
    """Read the pagination sections of a project as committed, in the order they were made.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param project_id: Project owning the sections.
    :type project_id: ProjectId
    :returns: The sections.
    :rtype: list[PaginationSection]
    """
    return list(await rig.open_uow().pagination_sections.list_for_project(project_id))


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


async def _project_with(rig: ImportRig, owner: Actor, policy: ImagePolicy) -> Project:
    """Store a project of the owner with no sources, under an image policy.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param owner: Account owning the project.
    :type owner: Actor
    :param policy: Image policy of the project.
    :type policy: ImagePolicy
    :returns: The stored project.
    :rtype: Project
    """
    project = evolve(make_project(owner_id=owner.account_id), image_policy=policy)
    await rig.fakes.store(project)
    return project


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

    :ivar policy: Image policy of the project.
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

    async def test_records_a_queued_job_with_its_files_and_enqueues_it(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify the upload is staged for the job, the job records its files in upload order, and the queue gets it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await fx_rig.service().start_import(fx_owner, fx_project.id, fx_book_files)

        stored = await fx_rig.stored_job(job)
        async with fx_rig.sources.staged_files(fx_project.id, job.id) as staged:
            staged_names = sorted(staged)
        expect(stored == job)
        expect((job.state, job.progress.total) == (JobState.QUEUED, 0))
        expect(
            job.request is not None
            and [file.name for file in job.request.files]
            == [
                'part3-cover.jpg',
                'part2.pdf',
                'part1.pdf',
            ]
        )
        expect(staged_names == ['part1.pdf', 'part2.pdf', 'part3-cover.jpg'])
        expect(fx_rig.queue.enqueued == [job])
        expect(_events_of(fx_rig, JobChanged)[0].job == job)
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self, fx_rig: ImportRig, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify an account cannot import into, or learn about, a project it does not own.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_project: Project of another account.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        stranger = Actor(account_id=new_account_id())
        with pytest.raises(NotFoundError):
            await fx_rig.service().start_import(stranger, fx_project.id, [image_upload(fx_samples, 'a.jpg')])
        assert fx_rig.queue.enqueued == []

    @pytest.mark.parametrize('state', [JobState.QUEUED, JobState.RUNNING], ids=str)
    async def test_project_importing_already_raises_conflict_before_receiving_anything(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path, tmp_path: Path, state: JobState
    ) -> None:
        """Verify a second upload is refused while an import is queued or running, before its files are streamed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        :param state: Active state of the import already there.
        :type state: JobState
        """
        project = make_project(owner_id=fx_owner.account_id)
        await fx_rig.fakes.store(project, make_job(project_id=project.id, state=state))

        with pytest.raises(ConflictError, match=IMPORT_ACTIVE):
            await fx_rig.service().start_import(fx_owner, project.id, [image_upload(fx_samples, 'a.jpg')])

        assert not (tmp_path / 'storage').exists()

    @pytest.mark.parametrize('state', [JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED], ids=str)
    async def test_finished_import_does_not_keep_the_project_from_importing_again(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path, state: JobState
    ) -> None:
        """Verify a project whose last import ended in any final state accepts a new upload.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param state: Final state of the earlier import.
        :type state: JobState
        """
        project = make_project(owner_id=fx_owner.account_id)
        await fx_rig.fakes.store(project, make_job(project_id=project.id, state=state))

        job = await fx_rig.service().start_import(fx_owner, project.id, [image_upload(fx_samples, 'a.jpg')])

        assert job.state is JobState.QUEUED

    @pytest.mark.parametrize(CASE_ARG, REFUSED_UPLOADS, ids=REFUSED_IDS)
    async def test_upload_that_breaks_a_rule_is_refused_and_leaves_nothing(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path, case: UploadCase
    ) -> None:
        """Verify each upload rule refuses with its problem, and no job, upload or queue entry is left.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
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
                fx_owner, fx_project.id, files
            )

        expect(refusal.value.problem is case.problem)
        expect(await fx_rig.open_uow().jobs.list_for_project(fx_project.id, set(JobState)) == [])
        expect(fx_rig.queue.enqueued == [])
        assert_expectations()

    @patch.object(RecordingJobQueue, 'enqueue', new=AsyncMock(side_effect=ConnectionError))
    async def test_unreachable_queue_fails_the_job_and_removes_the_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path, tmp_path: Path
    ) -> None:
        """Verify a job that could not be queued ends failed, so the project can import again, and loses its upload.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        with pytest.raises(ConnectionError):
            await fx_rig.service().start_import(fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])

        [job] = await fx_rig.open_uow().jobs.list_for_project(fx_project.id, set(JobState))
        expect((job.state, job.error) == (JobState.FAILED, NOT_QUEUED))
        expect(job.finished_at is not None)
        expect(not any((tmp_path / 'storage').rglob('a.jpg')))
        assert_expectations()


class TestAuthorizeUpload:
    """Tests for ImportService.authorize_upload(), which the API runs before it reads the body of a request."""

    async def test_owner_of_an_idle_project_may_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify the owner of a project with no import running is allowed, and nothing is stored or enqueued.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        await fx_rig.service().authorize_upload(fx_owner, fx_project.id)

        expect(await fx_rig.open_uow().jobs.list_for_project(fx_project.id, set(JobState)) == [])
        expect(fx_rig.queue.enqueued == [])
        assert_expectations()

    async def test_another_account_is_told_the_project_does_not_exist(
        self, fx_rig: ImportRig, fx_project: Project
    ) -> None:
        """Verify an account that does not own the project learns nothing about it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        with pytest.raises(NotFoundError):
            await fx_rig.service().authorize_upload(Actor(account_id=new_account_id()), fx_project.id)

    @pytest.mark.parametrize('state', [JobState.QUEUED, JobState.RUNNING], ids=str)
    async def test_project_that_imports_is_a_conflict(
        self, fx_rig: ImportRig, fx_owner: Actor, state: JobState
    ) -> None:
        """Verify a project with an import queued or running is refused with the conflict of the upload rule.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param state: Active state of the import already there.
        :type state: JobState
        """
        project = make_project(owner_id=fx_owner.account_id)
        await fx_rig.fakes.store(project, make_job(project_id=project.id, state=state))

        with pytest.raises(ConflictError, match=IMPORT_ACTIVE):
            await fx_rig.service().authorize_upload(fx_owner, project.id)


class TestRunImport:
    """Tests for ImportService.run_import()."""

    async def test_imports_every_file_as_a_source_of_its_own_with_its_pages_in_book_order(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify two PDF parts and a cover become three sources, and their scans become pages in the upload order.

        The files are uploaded out of the order of their names, and the book follows the upload.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await _import(fx_rig, fx_owner, fx_project.id, fx_book_files)

        sources, scans, pages = (
            await _sources(fx_rig, fx_project.id),
            await _scans(fx_rig, fx_project.id),
            await _pages(fx_rig, fx_project.id),
        )
        by_id = {scan.id: scan for scan in scans}
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == ['part3-cover.jpg', 'part2.pdf', 'part1.pdf'])
        expect(all(source.import_job_id == job.id for source in sources))
        expect([source.scan_count for source in sources] == [1, SECOND_PART_PAGES, FIRST_PART_PAGES])
        expect(job.result is not None and list(job.result.imported) == [source.id for source in sources])
        expect(len(scans) == len(pages) == SCAN_COUNT)
        expect(all(page.origin is PageOrigin.SCAN and page.scan_id in by_id for page in pages))
        expect(
            [(by_id[page.scan_id].source_id, by_id[page.scan_id].number) for page in pages if page.scan_id]
            == [(source.id, number) for source in sources for number in range(source.scan_count)]
        )
        assert_expectations()

    async def test_files_of_different_folders_with_one_name_are_two_sources_named_by_their_paths(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify ``vol1/001.tif`` and ``vol2/001.tif`` are two sources, kept under their paths and in upload order.

        Each source stores its file under the base name in a directory of its own, so the same name never clashes.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        second = image_upload(fx_samples, 'second.tif', width_px=SECOND_PART_WIDTH_PX)
        first = image_upload(fx_samples, 'first.tif')
        uploads = [
            upload('vol2/001.tif', content=second.file.read()),
            upload('vol1/001.tif', content=first.file.read()),
        ]

        job = await _import(fx_rig, fx_owner, fx_project.id, uploads)

        sources = await _sources(fx_rig, fx_project.id)
        stored = []
        for source in sources:
            async with fx_rig.sources.source_files(fx_project.id, source.id) as files:
                stored.append([path.name for path in files])
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == ['vol2/001.tif', 'vol1/001.tif'])
        expect([[file.name for file in source.files] for source in sources] == [['vol2/001.tif'], ['vol1/001.tif']])
        expect(stored == [['001.tif'], ['001.tif']])
        expect(job.result is not None and job.result.rejected == ())
        assert_expectations()

    async def test_system_files_of_a_directory_are_named_in_the_result_and_do_not_stop_the_import(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the files an operating system adds to a folder are rejected as such, in upload order, and the rest imports.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        scan = image_upload(fx_samples, 'scan.jpg')
        uploads = [
            upload('book/.DS_Store', content=b'folder view'),
            upload('book/001.jpg', content=scan.file.read()),
            upload('book/._001.jpg', content=b'resource fork'),
            upload('book/Thumbs.db', content=b'thumbnails'),
        ]

        job = await _import(fx_rig, fx_owner, fx_project.id, uploads)

        assert job.result is not None
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in await _sources(fx_rig, fx_project.id)] == ['book/001.jpg'])
        expect(
            [(file.file_name, file.reason) for file in job.result.rejected]
            == [
                ('book/.DS_Store', RejectionReason.SYSTEM_FILE),
                ('book/._001.jpg', RejectionReason.SYSTEM_FILE),
                ('book/Thumbs.db', RejectionReason.SYSTEM_FILE),
            ]
        )
        expect(job.result.skipped == ())
        assert_expectations()

    async def test_takes_the_page_labels_of_a_pdf_as_the_labels_of_its_scans_and_pages(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a PDF with Roman numerals for its preface and Arabic ones after gives its scans and pages those labels.

        An image file carries no labels, so its page stays unnumbered, whether it comes before the PDF or after it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, 'book.pdf', pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / 'book.pdf', rules=ROMAN_THEN_ARABIC_RULES)
        files = [upload('book.pdf', content=labelled.read_bytes()), image_upload(fx_samples, 'cover.jpg')]

        await _import(fx_rig, fx_owner, fx_project.id, files)

        scans, pages = await _scans(fx_rig, fx_project.id), await _pages(fx_rig, fx_project.id)
        expected = [*ROMAN_THEN_ARABIC_LABELS, '']
        expect([scan.source_label for scan in scans] == expected)
        expect([page.label for page in pages] == expected)
        assert_expectations()

    async def test_makes_a_pagination_section_of_each_page_label_rule_of_a_pdf(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the Roman and the Arabic rules become two printed sections that start at the pages the rules do.

        The labels the sections give are the labels of the file, so no page keeps its label as an exception.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, 'book.pdf', pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / 'book.pdf', rules=ROMAN_THEN_ARABIC_RULES)

        await _import(fx_rig, fx_owner, fx_project.id, [upload('book.pdf', content=labelled.read_bytes())])

        pages, sections = await _pages(fx_rig, fx_project.id), await _sections(fx_rig, fx_project.id)
        by_start = {section.first_page_id: section for section in sections}
        expect(len(sections) == len(ROMAN_THEN_ARABIC_RULES))
        expect(by_start[pages[0].id].style is LabelStyle.ROMAN_LOWER)
        expect(by_start[pages[2].id].style is LabelStyle.ARABIC)
        expect({section.display for section in sections} == {NumberDisplay.PRINTED})
        expect([page.label_manual for page in pages] == [False] * len(pages))
        assert_expectations()

    async def test_keeps_a_page_label_no_section_gives_as_an_exception(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a rule that writes a prefix and no number is a section that does not count, and its labels stay by hand.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        rules = [
            {'startpage': 0, 'prefix': '', 'style': 'r', 'firstpagenum': 1},
            {'startpage': 2, 'prefix': '12a', 'style': '', 'firstpagenum': 1},
        ]
        pdf_upload(fx_samples, 'book.pdf', pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / 'book.pdf', rules=rules)

        await _import(fx_rig, fx_owner, fx_project.id, [upload('book.pdf', content=labelled.read_bytes())])

        pages, sections = await _pages(fx_rig, fx_project.id), await _sections(fx_rig, fx_project.id)
        expect([page.label for page in pages] == ['i', 'ii', '12a', '12a'])
        expect([page.label_manual for page in pages] == [False, False, True, True])
        expect(
            sorted(section.display for section in sections)
            == sorted([NumberDisplay.PRINTED, NumberDisplay.NOT_COUNTED])
        )
        assert_expectations()

    async def test_a_source_without_page_labels_does_not_continue_the_numbering_of_a_labelled_pdf(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify an image that follows a labelled PDF is outside the count, in a section of its own that does not count.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, 'book.pdf', pages=len(ROMAN_THEN_ARABIC_LABELS))
        labelled = add_page_labels(fx_samples / 'book.pdf', rules=ROMAN_THEN_ARABIC_RULES)
        files = [upload('book.pdf', content=labelled.read_bytes()), image_upload(fx_samples, 'plate.jpg')]

        await _import(fx_rig, fx_owner, fx_project.id, files)

        pages, sections = await _pages(fx_rig, fx_project.id), await _sections(fx_rig, fx_project.id)
        [stopper] = [section for section in sections if section.first_page_id == pages[-1].id]
        expect([page.label for page in pages] == [*ROMAN_THEN_ARABIC_LABELS, ''])
        expect(stopper.display is NumberDisplay.NOT_COUNTED)
        expect(len(sections) == len(ROMAN_THEN_ARABIC_RULES) + 1)
        assert_expectations()

    async def test_a_book_without_sections_stays_without_them_when_a_source_has_no_page_labels(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify images alone make no section, so the book is numbered when the user adds its first one.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        files = [
            image_upload(fx_samples, 'cover.jpg'),
            image_upload(fx_samples, 'plate.jpg', width_px=PAGE_WIDTH_PX + 1),
        ]

        await _import(fx_rig, fx_owner, fx_project.id, files)

        assert await _sections(fx_rig, fx_project.id) == []

    async def test_a_later_source_with_page_labels_makes_sections_of_its_own(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a labelled PDF imported after another one gets its own sections, starting at its own first page.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        pdf_upload(fx_samples, 'one.pdf', pages=len(ROMAN_THEN_ARABIC_LABELS))
        first = add_page_labels(fx_samples / 'one.pdf', rules=ROMAN_THEN_ARABIC_RULES)
        pdf_upload(fx_samples, 'two.pdf', pages=len(ROMAN_THEN_ARABIC_LABELS), width_px=PAGE_WIDTH_PX + 1)
        second = add_page_labels(fx_samples / 'two.pdf', rules=ROMAN_THEN_ARABIC_RULES)

        await _import(fx_rig, fx_owner, fx_project.id, [upload('one.pdf', content=first.read_bytes())])
        await _import(fx_rig, fx_owner, fx_project.id, [upload('two.pdf', content=second.read_bytes())])

        pages, sections = await _pages(fx_rig, fx_project.id), await _sections(fx_rig, fx_project.id)
        starts = {section.first_page_id for section in sections}
        size = len(ROMAN_THEN_ARABIC_LABELS)
        expect(len(sections) == 2 * len(ROMAN_THEN_ARABIC_RULES))
        expect(starts == {pages[0].id, pages[2].id, pages[size].id, pages[size + 2].id})
        expect([page.label for page in pages] == [*ROMAN_THEN_ARABIC_LABELS, *ROMAN_THEN_ARABIC_LABELS])
        assert_expectations()

    async def test_writes_the_renditions_of_every_scan_and_the_base_version_of_every_page(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify each scan has its four renditions and each page a ready ``split.none`` version with its own four.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        await _import(fx_rig, fx_owner, fx_project.id, fx_book_files)

        keys, uow = ProjectKeys(fx_project.id), fx_rig.open_uow()
        scans, pages = await _scans(fx_rig, fx_project.id), await _pages(fx_rig, fx_project.id)
        missing: list[StorageKey] = []
        for scan in scans:
            missing += [
                key
                for rendition in (scan.renditions.full, *DERIVED)
                if not await _is_stored(fx_rig.assets, key := keys.scan_rendition(scan, rendition))
            ]
        versions = [version for page in pages for version in await uow.page_versions.list_for_page(page.id)]
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

    async def test_cuts_every_pyramid_for_the_route_that_serves_it(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify each pyramid is cut for the IIIF route and the key of its own directory, for scans and pages alike.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        await _import(fx_rig, fx_owner, fx_project.id, [image_upload(fx_samples, 'cover.jpg')])

        keys, uow = ProjectKeys(fx_project.id), fx_rig.open_uow()
        [scan] = await _scans(fx_rig, fx_project.id)
        [page] = await _pages(fx_rig, fx_project.id)
        [version] = await uow.page_versions.list_for_page(page.id)
        assert sorted(fx_rig.tiler.resource_ids) == sorted(
            [
                f'{IIIF_ROOT}/{keys.scan_rendition(scan, Rendition.TILES)}',
                f'{IIIF_ROOT}/{keys.version_rendition(version, Rendition.TILES)}',
            ]
        )

    async def test_announces_each_source_and_scan_and_the_result_of_the_job(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify the events a browser follows: the job, each source with its pages, each scan, and the final result.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await _import(fx_rig, fx_owner, fx_project.id, fx_book_files)

        published = fx_rig.fakes.events.published
        jobs = _events_of(fx_rig, JobChanged)
        first_scan = next(index for index, event in enumerate(published) if isinstance(event, ScanReady))
        last_source = max(index for index, event in enumerate(published) if isinstance(event, SourceImported))
        expect(len(_events_of(fx_rig, SourceImported)) == len(_events_of(fx_rig, PagesChanged)) == SOURCE_COUNT)
        expect(len(_events_of(fx_rig, ScanReady)) == SCAN_COUNT)
        expect({event.change for event in _events_of(fx_rig, PagesChanged)} == {PageChange.ADDED})
        expect(sum(len(event.page_ids) for event in _events_of(fx_rig, PagesChanged)) == SCAN_COUNT)
        # Every source is announced before the first image is, so the browser knows the book before it is cut
        expect(last_source < first_scan)
        expect([event.job.state for event in jobs][:2] == [JobState.QUEUED, JobState.RUNNING])
        expect(jobs[-1].job == job and jobs[-1].job.result is not None)
        expect(published[-1] == jobs[-1])
        assert_expectations()

    async def test_counts_the_scans_in_the_progress_of_the_job(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify the job's total is the number of scans of the upload and its done count reaches it.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_book_files: A book of two PDF parts and a cover, uploaded out of order.
        :type fx_book_files: list[UploadFile]
        """
        job = await _import(fx_rig, fx_owner, fx_project.id, fx_book_files)

        progresses = [(event.job.progress.done, event.job.progress.total) for event in _events_of(fx_rig, JobChanged)]
        expect((job.progress.done, job.progress.total) == (SCAN_COUNT, SCAN_COUNT))
        expect(progresses == sorted(progresses, key=itemgetter(0)))
        expect(job.started_at is not None and job.finished_at is not None)
        assert_expectations()

    async def test_new_pages_join_the_end_of_a_book_that_has_pages(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a second upload appends its pages after the pages of the first, in upload order.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        await _import(fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'first.pdf', pages=2)])
        before = await _pages(fx_rig, fx_project.id)

        await _import(fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'second.pdf', pages=2, width_px=210)])

        pages = await _pages(fx_rig, fx_project.id)
        assert (pages[: len(before)], len(pages)) == (before, 2 * len(before))


class TestRunImportFullFormat:
    """Tests for the format ImportService.run_import() writes the ``full`` image of a scan and its pages in."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            FullFormatCase(ImagePolicy.COMPACT, BILEVEL_MODE, 'page.tif', Rendition.FULL_PNG),
            FullFormatCase(ImagePolicy.LOSSLESS, BILEVEL_MODE, 'page.tif', Rendition.FULL_PNG),
            FullFormatCase(ImagePolicy.COMPACT, GRAY_MODE, 'page.jpg', Rendition.FULL_JPEG),
            FullFormatCase(ImagePolicy.LOSSLESS, GRAY_MODE, 'page.jpg', Rendition.FULL_PNG),
            FullFormatCase(ImagePolicy.COMPACT, RGB_MODE, 'page.jpg', Rendition.FULL_JPEG),
            FullFormatCase(ImagePolicy.LOSSLESS, RGB_MODE, 'page.jpg', Rendition.FULL_PNG),
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
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param case: Image policy of the project, the page image to import, and the format its ``full`` must have.
        :type case: FullFormatCase
        """
        project = await _project_with(fx_rig, fx_owner, case.policy)

        await _import(fx_rig, fx_owner, project.id, [image_upload(fx_samples, case.name, mode=case.mode)])

        keys = ProjectKeys(project.id)
        [scan] = await _scans(fx_rig, project.id)
        [page] = await _pages(fx_rig, project.id)
        [version] = await fx_rig.open_uow().page_versions.list_for_page(page.id)
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
        """Verify a scan imported under ``compact`` stays a JPEG after the project turns ``lossless``.

        A scan imported afterwards follows the new policy, and the first keeps its recorded format, its key and its
        file, so no stored address changes.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        project = await _project_with(fx_rig, fx_owner, ImagePolicy.COMPACT)
        await _import(fx_rig, fx_owner, project.id, [image_upload(fx_samples, 'first.jpg')])
        [first] = await _scans(fx_rig, project.id)
        uow = fx_rig.open_uow()
        await uow.projects.update(evolve(project, image_policy=ImagePolicy.LOSSLESS))
        await uow.commit()

        second = image_upload(fx_samples, 'second.jpg', width_px=SECOND_PART_WIDTH_PX)
        await _import(fx_rig, fx_owner, project.id, [second])

        keys = ProjectKeys(project.id)
        scans = await _scans(fx_rig, project.id)
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
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param policy: Image policy of the project.
        :type policy: ImagePolicy
        """
        project = await _project_with(fx_rig, fx_owner, policy)
        bundle = write_djvu_bundle(fx_samples / 'book.djvu', pages=DJVU_PAGES)

        job = await _import(fx_rig, fx_owner, project.id, djvu_uploads([bundle]))

        keys, uow = ProjectKeys(project.id), fx_rig.open_uow()
        scans = await _scans(fx_rig, project.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(sorted(scan.facts.color_mode for scan in scans) == sorted(page.mode for page in DJVU_PAGES))
        for scan in scans:
            wanted = DJVU_FULL_IMAGES[policy, scan.facts.color_mode]
            expect(await _stored_image(fx_rig, keys.scan_rendition(scan, scan.renditions.full)) == wanted)
        for page in await _pages(fx_rig, project.id):
            [version] = await uow.page_versions.list_for_page(page.id)
            scan = next(scan for scan in scans if scan.id == page.scan_id)
            wanted = DJVU_FULL_IMAGES[policy, scan.facts.color_mode]
            expect(version.renditions is not None and version.renditions.full is scan.renditions.full)
            expect(await _stored_image(fx_rig, keys.version_rendition(version, scan.renditions.full)) == wanted)
        assert_expectations()

    async def test_keeps_an_indirect_document_as_one_source_with_its_scans_in_index_order(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify an index and its page files become one source of all four files, next to the PDF of the upload.

        The files are uploaded out of order. The source keeps every file under its own directory, so ``ddjvu`` finds
        the pages beside the index when the scans are cut.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        files = write_djvu_indirect(fx_samples, pages=DJVU_PAGES, index_name='a-index.djvu')
        uploads = [pdf_upload(fx_samples, 'b-book.pdf'), *djvu_uploads(files[::-1])]

        job = await _import(fx_rig, fx_owner, fx_project.id, uploads)

        pdf, djvu = await _sources(fx_rig, fx_project.id)
        scans = [scan for scan in await _scans(fx_rig, fx_project.id) if scan.source_id == djvu.id]
        async with fx_rig.sources.source_files(fx_project.id, djvu.id) as stored:
            stored_names = sorted(path.name for path in stored)
        expect(job.state is JobState.SUCCEEDED)
        expect((djvu.kind, djvu.file_name, pdf.file_name) == (SourceKind.DJVU, 'a-index.djvu', 'b-book.pdf'))
        expect([file.name for file in djvu.files] == [path.name for path in files])
        expect(djvu.size_bytes == sum(path.stat().st_size for path in files))
        expect(stored_names == sorted(path.name for path in files))
        expect((djvu.scan_count, djvu.metadata[FactKey.DJVU_KIND]) == (len(DJVU_PAGES), DjvuDocumentKind.INDIRECT))
        expect([scan.facts.width_px for scan in sorted(scans, key=attrgetter('number'))] == DJVU_WIDTHS)
        expect(all(scan.renditions.ready for scan in scans))
        assert_expectations()

    async def test_makes_a_source_of_every_bundled_part_and_orders_the_pages_by_the_order_of_the_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify two bundled parts are two sources, whose pages follow the upload and not the names of the parts.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        second = write_djvu_bundle(fx_samples / 'part2.djvu', pages=DJVU_PAGES[:2])
        tenth = write_djvu_bundle(fx_samples / 'part10.djvu', pages=DJVU_PAGES[1:])

        await _import(fx_rig, fx_owner, fx_project.id, djvu_uploads([tenth, second]))

        sources, scans, pages = (
            await _sources(fx_rig, fx_project.id),
            await _scans(fx_rig, fx_project.id),
            await _pages(fx_rig, fx_project.id),
        )
        widths = {scan.id: scan.facts.width_px for scan in scans}
        expect([source.file_name for source in sources] == ['part10.djvu', 'part2.djvu'])
        expect([source.scan_count for source in sources] == [2, 2])
        expect([widths[page.scan_id] for page in pages if page.scan_id] == [*DJVU_WIDTHS[1:], *DJVU_WIDTHS[:2]])
        assert_expectations()

    async def test_makes_a_source_of_every_single_page_file(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify every single-page file is a source of its own with one scan.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        files = write_djvu_pages(fx_samples, pages=DJVU_PAGES)

        await _import(fx_rig, fx_owner, fx_project.id, djvu_uploads(files))

        sources = await _sources(fx_rig, fx_project.id)
        expect([source.file_name for source in sources] == [path.name for path in files])
        expect([source.scan_count for source in sources] == [1] * len(files))
        assert_expectations()

    async def test_rejects_an_indirect_document_that_lacks_files_and_imports_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify an incomplete indirect document is rejected whole, naming the missing files, and the PDF is imported.

        The page file that is there is not imported alone, and nothing of the rejected document stays staged.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        index, first, *_ = write_djvu_indirect(fx_samples, pages=DJVU_PAGES, index_name='a-index.djvu')

        job = await _import(
            fx_rig, fx_owner, fx_project.id, [*djvu_uploads([index, first]), pdf_upload(fx_samples, 'b-book.pdf')]
        )

        assert job.result is not None
        [rejected] = job.result.rejected
        expect(job.state is JobState.SUCCEEDED)
        expect((rejected.file_name, rejected.reason) == ('a-index.djvu', RejectionReason.UNREADABLE))
        expect('p0002.djvu, p0003.djvu' in rejected.detail)
        expect([source.file_name for source in await _sources(fx_rig, fx_project.id)] == ['b-book.pdf'])
        assert_expectations()


class TestRunImportChecks:
    """Tests for the checks ImportService.run_import() puts each file through, and what a source gives the book."""

    async def test_rejects_a_file_the_project_already_has_and_imports_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a second copy of a file, under any name, is rejected with the name of the source it repeats.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        content = pdf_upload(fx_samples, 'book.pdf').file.read()

        job = await _import(
            fx_rig,
            fx_owner,
            fx_project.id,
            [
                upload('a-book.pdf', content=content),
                upload('b-copy.pdf', content=content),
                image_upload(fx_samples, 'c.jpg'),
            ],
        )

        assert job.result is not None
        [rejected] = job.result.rejected
        expect(job.state is JobState.SUCCEEDED)
        expect([source.file_name for source in await _sources(fx_rig, fx_project.id)] == ['a-book.pdf', 'c.jpg'])
        expect((rejected.file_name, rejected.reason) == ('b-copy.pdf', RejectionReason.DUPLICATE))
        expect('a-book.pdf' in rejected.detail)
        assert_expectations()

    async def test_rejects_a_file_of_an_earlier_upload_and_fails_a_job_that_imports_nothing(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify uploading the same file again imports nothing, so the job fails with the reason of each rejection.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        content = image_upload(fx_samples, 'cover.jpg').file.read()
        await _import(fx_rig, fx_owner, fx_project.id, [upload('cover.jpg', content=content)])

        job = await _import(fx_rig, fx_owner, fx_project.id, [upload('again.jpg', content=content)])

        assert job.result is not None
        expect((job.state, job.error) == (JobState.FAILED, NO_SOURCE_IMPORTED))
        expect(
            [(file.file_name, file.reason) for file in job.result.rejected]
            == [('again.jpg', RejectionReason.DUPLICATE)]
        )
        expect(len(await _sources(fx_rig, fx_project.id)) == 1)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, BAD_FILES, ids=BAD_FILE_IDS)
    async def test_rejects_a_file_no_source_can_be_made_of_and_imports_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path, case: BadFile
    ) -> None:
        """Verify one bad file is rejected with its reason and text while the good file of the upload is imported.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param case: A file no source can be made of, with the reason it is rejected for.
        :type case: BadFile
        """
        job = await _import(
            fx_rig,
            fx_owner,
            fx_project.id,
            [upload(case.name, content=case.content), image_upload(fx_samples, 'page.jpg')],
        )

        assert job.result is not None
        [rejected] = job.result.rejected
        expect(job.state is JobState.SUCCEEDED)
        expect((rejected.file_name, rejected.reason) == (case.name, case.reason))
        expect(rejected.detail != '')
        expect([source.file_name for source in await _sources(fx_rig, fx_project.id)] == ['page.jpg'])
        assert_expectations()

    async def test_job_that_imports_nothing_fails_and_lists_every_rejection(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify an upload of only bad files fails the job with a message, and keeps no source or page.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = await _import(
            fx_rig,
            fx_owner,
            fx_project.id,
            [upload('bad.pdf', content=BROKEN_PDF), upload('notes.txt', content=BROKEN_PDF)],
        )

        assert job.result is not None
        expect((job.state, job.error) == (JobState.FAILED, NO_SOURCE_IMPORTED))
        expect(sorted(file.file_name for file in job.result.rejected) == ['bad.pdf', 'notes.txt'])
        expect(list(job.result.imported) == [])
        expect(await _pages(fx_rig, fx_project.id) == [])
        assert_expectations()

    async def test_fills_empty_fields_of_the_description_from_the_first_source_that_has_them(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a source fills the empty author, a later source does not replace it, and the title stays.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        await _import(
            fx_rig,
            fx_owner,
            fx_project.id,
            [
                pdf_upload(fx_samples, 'a.pdf', pages=1, metadata={'title': 'Other title', 'author': 'First Author'}),
                pdf_upload(fx_samples, 'b.pdf', pages=1, width_px=210, metadata={'author': 'Second Author'}),
            ],
        )

        project = await fx_rig.open_uow().projects.get(fx_project.id)
        expect(project.details.title == fx_project.details.title)
        expect(project.details.contributors == (Contributor(name='First Author', role=ContributorRole.AUTHOR),))
        expect(len(_events_of(fx_rig, ProjectChanged)) == 1)
        assert_expectations()

    async def test_fills_the_lists_of_the_description_from_the_xmp_of_a_source(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the languages, identifiers and subjects of an XMP packet reach an empty description, in one change.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        packet = xmp_packet(
            '<dc:language><rdf:Bag><rdf:li>be</rdf:li></rdf:Bag></dc:language>'
            '<dc:identifier>urn:isbn:0-306-40615-2</dc:identifier>'
            '<dc:subject><rdf:Bag><rdf:li>Folklore</rdf:li></rdf:Bag></dc:subject>'
        )

        path = add_xmp(write_pdf(fx_samples / 'a.pdf', pages=[PdfPage()]), packet=packet)

        await _import(fx_rig, fx_owner, fx_project.id, [upload('a.pdf', content=path.read_bytes())])

        details = (await fx_rig.open_uow().projects.get(fx_project.id)).details
        expect((details.languages, details.subjects) == (('bel',), ('Folklore',)))
        expect([item.value for item in details.identifiers] == ['0306406152'])
        expect(len(_events_of(fx_rig, ProjectChanged)) == 1)
        assert_expectations()

    async def test_leaves_a_described_book_alone(self, fx_rig: ImportRig, fx_owner: Actor, fx_samples: Path) -> None:
        """Verify a field the owner filled is never replaced, and no change is announced when nothing was filled.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        project = make_project(owner_id=fx_owner.account_id)
        owner_author = (Contributor(name='Owner Author', role=ContributorRole.AUTHOR),)
        described = evolve(project, details=evolve(project.details, contributors=owner_author))
        await fx_rig.fakes.store(described)

        await _import(
            fx_rig,
            fx_owner,
            project.id,
            [pdf_upload(fx_samples, 'a.pdf', pages=1, metadata={'author': 'Document Author'})],
        )

        expect((await fx_rig.open_uow().projects.get(project.id)).details == described.details)
        expect(_events_of(fx_rig, ProjectChanged) == [])
        assert_expectations()


class TestRunImportLifecycle:
    """Tests for how ImportService.run_import() cuts, stops, continues and ends a job."""

    async def test_cuts_no_more_scans_at_once_than_the_limit_allows(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the scans are cut in parallel, but never more than ``parallel_scans`` at the same time.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        fx_rig.rasterizer.pause = OVERLAP_SECONDS

        job = await _import(
            fx_rig,
            fx_owner,
            fx_project.id,
            [pdf_upload(fx_samples, 'a.pdf', pages=LONG_BOOK_PAGES)],
            parallel_scans=PARALLEL_SCANS,
        )

        expect(job.state is JobState.SUCCEEDED)
        expect(fx_rig.rasterizer.peak == PARALLEL_SCANS)
        expect(sorted(fx_rig.rasterizer.extracted) == list(range(LONG_BOOK_PAGES)))
        assert_expectations()

    async def test_job_cancelled_between_sources_keeps_the_sources_it_committed(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path, tmp_path: Path
    ) -> None:
        """Verify a cancelled job stops before the next source, names the files it never reached, and removes them.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        job = await fx_rig.service().start_import(
            fx_owner,
            fx_project.id,
            [pdf_upload(fx_samples, 'a.pdf', pages=1), pdf_upload(fx_samples, 'b.pdf', pages=1, width_px=210)],
        )

        async def cancel_before_the_second_source() -> None:
            """Cancel the job when the second source is about to be inspected."""
            if len(fx_rig.inspector.inspected) == 1:
                await fx_rig.fakes.job_service().cancel(fx_owner, job.id)

        fx_rig.inspector.before_inspect = cancel_before_the_second_source

        await fx_rig.service().run_import(job.id)

        stored = await fx_rig.stored_job(job)
        assert stored.result is not None
        expect(stored.state is JobState.CANCELLED)
        expect([source.file_name for source in await _sources(fx_rig, fx_project.id)] == ['a.pdf'])
        expect(list(stored.result.skipped) == ['b.pdf'])
        expect(len(stored.result.imported) == 1)
        expect(not any((tmp_path / 'storage').rglob('incoming/*')))
        expect(_events_of(fx_rig, JobChanged)[-1].job == stored)
        assert_expectations()

    async def test_job_cancelled_between_scans_stops_before_the_next_scan_and_the_next_job_cuts_the_rest(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify scans left without renditions by a cancelled job are cut first by the project's next import.

        The cancelled job keeps its source and pages. The next job writes the directories the cancelled one left
        half written, since a stored file is never replaced, and counts those scans in its own progress.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await fx_rig.service().start_import(
            fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=THREE_PAGES)]
        )

        async def cancel_after_the_first_scan(event: DomainEvent) -> None:
            """Cancel the job once, as the first scan is announced and the second is not started.

            :param event: Event just delivered.
            :type event: DomainEvent
            """
            if isinstance(event, ScanReady):
                fx_rig.events.after_publish = None
                await fx_rig.fakes.job_service().cancel(fx_owner, job.id)

        fx_rig.events.after_publish = cancel_after_the_first_scan

        await fx_rig.service(parallel_scans=1).run_import(job.id)

        cancelled = await fx_rig.stored_job(job)
        expect(cancelled.state is JobState.CANCELLED)
        # The second scan is never started, since the job is found cancelled before it
        expect(fx_rig.rasterizer.extracted == [0])
        expect([scan.renditions.ready for scan in await _scans(fx_rig, fx_project.id)] == [True, False, False])
        expect(len(await _pages(fx_rig, fx_project.id)) == THREE_PAGES)

        next_job = await _import(fx_rig, fx_owner, fx_project.id, [image_upload(fx_samples, 'cover.jpg')])

        expect(next_job.state is JobState.SUCCEEDED)
        expect(all(scan.renditions.ready for scan in await _scans(fx_rig, fx_project.id)))
        # The two scans the cancelled job left are counted with the one scan of the new upload
        expect((next_job.progress.done, next_job.progress.total) == (THREE_PAGES, THREE_PAGES))
        assert_expectations()

    async def test_job_delivered_again_after_a_crash_continues_without_duplicates(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a running job found again keeps its sources and pages, and cuts only the scans still unready.

        The worker dies while it cuts the second scan, after the scan's ``full`` image was published, so the second
        delivery has to write that directory again.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await fx_rig.service().start_import(
            fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=THREE_PAGES)]
        )
        # The first preview is the first scan's and the second is its page's, so the third is the second scan's
        fx_rig.tiler.crash_on_preview = 3
        with pytest.raises(BaseExceptionGroup):
            await fx_rig.service(parallel_scans=1).run_import(job.id)
        crashed = await fx_rig.stored_job(job)
        sources, pages = await _sources(fx_rig, fx_project.id), await _pages(fx_rig, fx_project.id)

        await fx_rig.service().run_import(job.id)

        finished = await fx_rig.stored_job(job)
        expect(crashed.state is JobState.RUNNING)
        expect(finished.state is JobState.SUCCEEDED)
        expect(finished.started_at == crashed.started_at)
        expect(await _sources(fx_rig, fx_project.id) == sources)
        expect(await _pages(fx_rig, fx_project.id) == pages)
        expect(all(scan.renditions.ready for scan in await _scans(fx_rig, fx_project.id)))
        expect((finished.progress.done, finished.progress.total) == (THREE_PAGES, THREE_PAGES))
        expect(finished.result is not None and list(finished.result.imported) == [source.id for source in sources])
        assert_expectations()

    async def test_job_delivered_again_promotes_the_files_of_a_source_committed_before_a_crash(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the upload is not lost when the worker dies between the commit of a source and its promotion.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await fx_rig.service().start_import(fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])
        fx_rig.sources.crash_on_promote = True
        with pytest.raises(WorkerCrashError):
            await fx_rig.service().run_import(job.id)

        await fx_rig.service().run_import(job.id)

        [source] = await _sources(fx_rig, fx_project.id)
        async with fx_rig.sources.source_files(fx_project.id, source.id) as files:
            stored_names = [path.name for path in files]
        expect((await fx_rig.stored_job(job)).state is JobState.SUCCEEDED)
        expect(stored_names == ['a.jpg'])
        assert_expectations()

    async def test_job_that_finished_only_has_its_upload_removed(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a job cancelled while it was queued imports nothing, stays cancelled, and leaves no upload behind.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await fx_rig.service().start_import(fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])
        cancelled = await fx_rig.fakes.job_service().cancel(fx_owner, job.id)

        await fx_rig.service().run_import(job.id)

        expect(await fx_rig.stored_job(job) == cancelled)
        expect(await _sources(fx_rig, fx_project.id) == [])
        with pytest.raises(NotFoundError):
            async with fx_rig.sources.staged_files(fx_project.id, job.id):
                pass
        assert_expectations()

    async def test_missing_job_raises_not_found(self, fx_rig: ImportRig) -> None:
        """Verify a task for a job that is not stored is reported, not ignored.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        """
        with pytest.raises(NotFoundError):
            await fx_rig.service().run_import(make_job(project_id=make_project(owner_id=new_account_id()).id).id)

    async def test_damaged_scan_fails_the_job_with_the_message_of_the_error(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path, tmp_path: Path
    ) -> None:
        """Verify a scan that cannot be written fails the job with the reason, and the upload is removed.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        fx_rig.rasterizer.failures[1] = UnsupportedSourceError('Page 2 of a.pdf is damaged.')

        job = await _import(
            fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=THREE_PAGES)], parallel_scans=1
        )

        assert job.result is not None
        expect((job.state, job.error) == (JobState.FAILED, 'Page 2 of a.pdf is damaged.'))
        expect(len(job.result.imported) == 1)
        expect(not any((tmp_path / 'storage').rglob('incoming/*')))
        assert_expectations()

    async def test_unexpected_error_fails_the_job_without_leaking_its_message(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify an error nobody foresaw ends the job as failed with a fixed message, so the project is not stuck.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        fx_rig.rasterizer.failures[0] = RuntimeError(SECRET_FAULT)

        job = await _import(fx_rig, fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])

        expect((job.state, job.error) == (JobState.FAILED, UNEXPECTED_FAILURE))
        expect((await fx_rig.open_uow().jobs.list_for_project(fx_project.id, JobState.active())) == [])
        assert_expectations()


class TestRunImportFailures:
    """Tests for what ImportService.run_import() leaves behind when a step fails or races another."""

    async def test_job_cancelled_before_it_starts_has_its_upload_removed(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path, tmp_path: Path
    ) -> None:
        """Verify a job cancelled between the worker reading it and starting it leaves no upload behind.

        Nothing delivers a cancelled job again, so the run that found it cancelled is the only one that can remove the
        files the job received.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        job = await fx_rig.service().start_import(fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])
        read = InMemoryJobRepository.get
        armed: list[bool] = []
        cancelled: list[Job] = []

        async def get_then_cancel(repository: InMemoryJobRepository, job_id: JobId) -> Job:
            """Read the job as the worker does, and let the account holder cancel it right after, once.

            :param repository: Repository the worker reads from.
            :type repository: InMemoryJobRepository
            :param job_id: Identifier of the job.
            :type job_id: JobId
            :returns: The job as it was read, still queued.
            :rtype: Job
            """
            stored = await read(repository, job_id)
            if not armed:
                # Disarmed first, since cancelling reads the job through this same method
                armed.append(True)
                cancelled.append(await fx_rig.fakes.job_service().cancel(fx_owner, job_id))
            return stored

        with patch.object(InMemoryJobRepository, 'get', get_then_cancel):
            await fx_rig.service().run_import(job.id)

        expect(await fx_rig.stored_job(job) == cancelled[0])
        expect(await _sources(fx_rig, fx_project.id) == [])
        expect(not any((tmp_path / 'storage').rglob('incoming/*')))
        assert_expectations()

    async def test_job_that_another_delivery_started_keeps_its_upload(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a delivery that lost the race to start the job leaves the files the winner is importing.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await fx_rig.service().start_import(fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])
        read = InMemoryJobRepository.get
        started: list[bool] = []

        async def get_then_let_another_delivery_start(repository: InMemoryJobRepository, job_id: JobId) -> Job:
            """Read the job as this delivery does, and let another delivery start it right after, once.

            :param repository: Repository this delivery reads from.
            :type repository: InMemoryJobRepository
            :param job_id: Identifier of the job.
            :type job_id: JobId
            :returns: The job as it was read, still queued.
            :rtype: Job
            """
            stored = await read(repository, job_id)
            if not started:
                started.append(True)
                other = fx_rig.open_uow()
                await other.jobs.update_if_state(evolve(stored, state=JobState.RUNNING), expected=(JobState.QUEUED,))
                await other.commit()
            return stored

        with patch.object(InMemoryJobRepository, 'get', get_then_let_another_delivery_start):
            await fx_rig.service().run_import(job.id)

        async with fx_rig.sources.staged_files(fx_project.id, job.id) as staged:
            expect(list(staged) == ['a.jpg'])
        expect((await fx_rig.stored_job(job)).state is JobState.RUNNING)
        assert_expectations()

    async def test_source_whose_files_cannot_be_promoted_is_withdrawn_whole(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a failed promotion leaves no source without files: no source, scan or page, and no counted scans.

        The source was committed before its files moved, so the run takes it back with its pages, which a bare delete
        of the source would leave behind without a scan.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        fx_rig.sources.fail_on_promote = OSError('disk full')

        job = await _import(fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=THREE_PAGES)])

        expect(job.state is JobState.FAILED)
        expect(job.error == UNEXPECTED_FAILURE)
        expect(await _sources(fx_rig, fx_project.id) == [])
        expect(await _scans(fx_rig, fx_project.id) == [])
        expect(await _pages(fx_rig, fx_project.id) == [])
        expect((job.progress.done, job.progress.total) == (0, 0))
        expect(job.result is not None and list(job.result.imported) == [])
        assert_expectations()

    async def test_failed_job_counts_only_the_scans_that_were_committed(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a step that fails before its commit leaves its scans out of the progress of the failed job.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        failure = UnsupportedSourceError('scans refused')
        with patch.object(InMemoryScanRepository, 'add_many', AsyncMock(side_effect=failure)):
            job = await _import(fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=THREE_PAGES)])

        stored = await fx_rig.open_uow().jobs.get(job.id)
        expect((job.state, job.error) == (JobState.FAILED, 'scans refused'))
        expect((job.progress.done, job.progress.total) == (0, 0))
        expect(stored.progress == job.progress)
        expect(await _sources(fx_rig, fx_project.id) == [])
        assert_expectations()

    async def test_real_failure_is_reported_when_another_scan_finds_the_job_cancelled(
        self,
        fx_rig: ImportRig,
        fx_owner: Actor,
        fx_project: Project,
        fx_samples: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Verify two scans that stop at the same moment, one on a cancellation and one on a fault, fail the job.

        The cancellation must not hide the fault, which would end the job as cancelled and leave nothing in the log.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        :param caplog: Fixture capturing what the service logs.
        :type caplog: pytest.LogCaptureFixture
        """
        second_arrived = asyncio.Event()
        arrivals: list[Scan] = []

        async def stop_together(_run: ImportRun, _source: Source, scan: Scan) -> None:
            """Stop two scans in the same turn of the event loop, the scan that arrives first on a cancellation.

            The first scan waits for the second, which releases it and gives up its turn once, so the first scan
            wakes and stops before the second does, and both stop before the group can cancel the second. The group
            then lists the cancellation before the fault. The scans stop at once, outside any cleanup, because a scan
            that unwinds through the asset store awaits, and the group may cancel it before its fault is listed.

            :param _run: Run cutting the scan, which the patched step replaces.
            :type _run: ImportRun
            :param _source: Source holding the scan.
            :type _source: Source
            :param scan: The scan to cut.
            :type scan: Scan
            :raises ImportCancelledError: For the scan that arrives first.
            :raises RuntimeError: For the scan that arrives second.
            """
            arrivals.append(scan)
            if len(arrivals) < PARALLEL_SCANS:
                await second_arrived.wait()
                raise ImportCancelledError
            second_arrived.set()
            await asyncio.sleep(0)
            raise RuntimeError(SECRET_FAULT)

        with patch.object(ImportRun, '_cut', stop_together):
            job = await _import(
                fx_rig,
                fx_owner,
                fx_project.id,
                [pdf_upload(fx_samples, 'a.pdf', pages=PARALLEL_SCANS)],
                parallel_scans=PARALLEL_SCANS,
            )

        expect((job.state, job.error) == (JobState.FAILED, UNEXPECTED_FAILURE))
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

    async def _run_import(
        self, rig: ImportRig, actor: Actor, project_id: ProjectId, files: list[UploadFile], recipes: DefaultRecipes
    ) -> Job:
        """Upload the files and run the job in a service whose catalogue offers ``split.none`` and ``split.auto``.

        :param rig: Adapters of the import.
        :type rig: ImportRig
        :param actor: Account acting in the request.
        :type actor: Actor
        :param project_id: Project to import into.
        :type project_id: ProjectId
        :param files: The uploaded files.
        :type files: list[UploadFile]
        :param recipes: The recipes the stages start with.
        :type recipes: DefaultRecipes
        :returns: The job as stored after the run.
        :rtype: Job
        """
        offering = evolve(rig, defaults=recipes, processors=[SplitNone(), AutoSplitProcessor()])
        job = await offering.service().start_import(actor, project_id, files)
        await offering.service().run_import(job.id)
        return await offering.stored_job(job)

    async def test_queues_a_split_of_the_new_pages_when_the_recipe_is_automatic(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the finished import queues one run of the page split naming the pages of the sources it imported.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await self._run_import(
            fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=2)], self._recipes(AUTO_SPLIT)
        )

        [queued] = [queued for queued in fx_rig.queue.enqueued if queued.kind is JobKind.RUN_STAGE]
        pages = await _pages(fx_rig, fx_project.id)
        expect(job.state is JobState.SUCCEEDED)
        expect(
            StageRun.from_map(queued.params) == StageRun(stage=Stage.PAGE_SPLIT, page_ids=tuple(p.id for p in pages))
        )
        expect(queued.project_id == fx_project.id)
        assert_expectations()

    async def test_a_later_import_queues_only_its_own_pages(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify the pages of an earlier import, which the user may have changed, are left out of the next run.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        recipes = self._recipes(AUTO_SPLIT)
        await self._run_import(fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=2)], recipes)
        first_run = next(job for job in fx_rig.queue.enqueued if job.kind is JobKind.RUN_STAGE)
        uow = fx_rig.open_uow()
        await uow.jobs.update_if_state(evolve(first_run, state=JobState.SUCCEEDED), expected=(JobState.QUEUED,))
        await uow.commit()
        await self._run_import(
            fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'b.pdf', pages=1, width_px=210)], recipes
        )

        runs = [StageRun.from_map(job.params) for job in fx_rig.queue.enqueued if job.kind is JobKind.RUN_STAGE]
        pages = await _pages(fx_rig, fx_project.id)
        assert [run.page_ids for run in runs][1] == (pages[-1].id,)

    async def test_queues_nothing_when_the_recipe_was_chosen_by_hand(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a book whose page split recipe is not the automatic one is not split by the import.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        job = await self._run_import(
            fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=2)], self._recipes('split.none')
        )

        expect(job.state is JobState.SUCCEEDED)
        expect([queued for queued in fx_rig.queue.enqueued if queued.kind is JobKind.RUN_STAGE] == [])
        assert_expectations()

    async def test_a_split_that_cannot_be_queued_does_not_fail_the_import(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_samples: Path
    ) -> None:
        """Verify a project that is processing something already still ends its import as succeeded.

        :param fx_rig: Adapters of the import.
        :type fx_rig: ImportRig
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param fx_samples: Directory the sample files are built in.
        :type fx_samples: Path
        """
        uow = fx_rig.open_uow()
        await uow.jobs.add(
            evolve(make_job(project_id=fx_project.id, state=JobState.RUNNING), kind=JobKind.COLLECT_VERSIONS)
        )
        await uow.commit()

        job = await self._run_import(
            fx_rig, fx_owner, fx_project.id, [pdf_upload(fx_samples, 'a.pdf', pages=1)], self._recipes(AUTO_SPLIT)
        )

        expect(job.state is JobState.SUCCEEDED)
        expect([queued for queued in fx_rig.queue.enqueued if queued.kind is JobKind.RUN_STAGE] == [])
        assert_expectations()
