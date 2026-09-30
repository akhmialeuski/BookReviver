"""Tests for the import service: receiving an upload, and running the job it becomes, source by source."""

from operator import itemgetter
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import (
    JobState,
    PageOrigin,
    RejectionReason,
    Rendition,
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
from bookreviver.domain.values import SliceRequest
from bookreviver.services.imports import (
    IMPORT_ACTIVE,
    NO_SOURCE_IMPORTED,
    NOT_QUEUED,
    SPLIT_NONE,
    UNEXPECTED_FAILURE,
)
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_imports import (
    IIIF_ROOT,
    OVERLAP_SECONDS,
    ImportRig,
    WorkerCrashError,
    image_upload,
    pdf_upload,
)
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import UploadFile

    from bookreviver.domain.entities import Job, Page, Project, Scan, Source
    from bookreviver.domain.ids import ProjectId, StorageKey
    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

FILES_ARG: str = 'files'
CASE_ARG: str = 'case'
RENDITIONS: tuple[Rendition, ...] = (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL, Rendition.TILES)
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
BROKEN_PDF: bytes = b'this is not a pdf'
DJVU_HEADER: bytes = b'AT&TFORM'


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


async def _import(rig: ImportRig, actor: Actor, project_id: ProjectId, files: list[UploadFile], **limits: int) -> Job:
    """Upload the files and run the job, each in a service of its own as a request and a worker would.

    :param rig: Adapters of the import.
    :type rig: ImportRig
    :param actor: Account acting in the request.
    :type actor: Actor
    :param project_id: Project to import into.
    :type project_id: ProjectId
    :param files: The uploaded files.
    :type files: list[UploadFile]
    :param limits: Keyword arguments of ``ImportRig.service`` for the service that runs the job.
    :type limits: int
    :returns: The job as stored after the run.
    :rtype: Job
    """
    job = await rig.service().start_import(actor, project_id, files)
    await rig.service(**limits).run_import(job.id)
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
    :ivar limits: Keyword arguments of ``ImportRig.service`` that set the limits of the upload.
    :ivar problem: Rule the upload breaks.
    """

    names: list[str | None]
    limits: dict[str, int]
    problem: UploadProblem


REFUSED_UPLOADS: list[UploadCase] = [
    UploadCase(names=[], limits={}, problem=UploadProblem.NO_FILES),
    UploadCase(names=['a.jpg', 'b.jpg', 'c.jpg'], limits={'max_files': 2}, problem=UploadProblem.TOO_MANY_FILES),
    UploadCase(names=['a.jpg'], limits={'max_bytes': 3}, problem=UploadProblem.TOO_LARGE),
    UploadCase(names=[None], limits={}, problem=UploadProblem.EMPTY_NAME),
    UploadCase(names=['a.jpg', 'A.JPG'], limits={}, problem=UploadProblem.DUPLICATE_NAME),
]
REFUSED_IDS: list[str] = ['no-files', 'too-many-files', 'too-large', 'empty-name', 'duplicate-name']


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
]
BAD_FILE_IDS: list[str] = ['damaged-pdf', 'damaged-image', 'djvu-not-readable-yet', 'unsupported-type']


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
            staged_names = sorted(path.name for path in staged)
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
            await fx_rig.service(**case.limits).start_import(fx_owner, fx_project.id, files)

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


class TestRunImport:
    """Tests for ImportService.run_import()."""

    async def test_imports_every_file_as_a_source_of_its_own_with_its_pages_in_book_order(
        self, fx_rig: ImportRig, fx_owner: Actor, fx_project: Project, fx_book_files: list[UploadFile]
    ) -> None:
        """Verify two PDF parts and a cover become three sources, and their scans become pages in the order of names.

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
        expect([source.file_name for source in sources] == ['part1.pdf', 'part2.pdf', 'part3-cover.jpg'])
        expect(all(source.import_job_id == job.id for source in sources))
        expect([source.scan_count for source in sources] == [FIRST_PART_PAGES, SECOND_PART_PAGES, 1])
        expect(job.result is not None and list(job.result.imported) == [source.id for source in sources])
        expect(len(scans) == len(pages) == SCAN_COUNT)
        expect(all(page.origin is PageOrigin.SCAN and page.scan_id in by_id for page in pages))
        expect(
            [(by_id[page.scan_id].source_id, by_id[page.scan_id].number) for page in pages if page.scan_id]
            == [(source.id, number) for source in sources for number in range(source.scan_count)]
        )
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
                for rendition in RENDITIONS
                if not await _is_stored(fx_rig.assets, key := keys.scan_rendition(scan, rendition))
            ]
        versions = [version for page in pages for version in await uow.page_versions.list_for_page(page.id)]
        for version in versions:
            missing += [
                key
                for rendition in RENDITIONS
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
                and version.id == version.identify(page_id=version.page_id, processor=SPLIT_NONE)
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
        expect(project.details.authors == 'First Author')
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
        described = evolve(project, details=evolve(project.details, authors='Owner Author'))
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
        fx_rig.rasterizer.failures[0] = RuntimeError('secret detail of the failure')

        job = await _import(fx_rig, fx_owner, fx_project.id, [image_upload(fx_samples, 'a.jpg')])

        expect((job.state, job.error) == (JobState.FAILED, UNEXPECTED_FAILURE))
        expect((await fx_rig.open_uow().jobs.list_for_project(fx_project.id, JobState.active())) == [])
        assert_expectations()
