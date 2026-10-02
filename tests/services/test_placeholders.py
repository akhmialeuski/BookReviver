"""Tests for the use cases that add and delete pages without a scan, bind scans, and write their images."""

from typing import TYPE_CHECKING, NamedTuple, override
from unittest.mock import AsyncMock, patch
from uuid import UUID

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import (
    JobKind,
    JobState,
    NewPageOrigin,
    PageChange,
    PageKind,
    PageOrigin,
    Rendition,
    Side,
    Stage,
    VersionData,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotAPlaceholderError, NotFoundError, ScanAlreadyInBookError
from bookreviver.domain.events import JobChanged, PagesChanged, PageVersionReady
from bookreviver.domain.ids import PageId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPage, PageAnchor, PageSize, Renditions
from bookreviver.ports.runtime import JobQueue
from bookreviver.services.base_versions import PAGES_BLANK, SPLIT_NONE, BaseVersions
from bookreviver.services.pages import NOT_QUEUED, UNEXPECTED_FAILURE
from tests.helpers.books import IMAGE
from tests.helpers.builders import EPOCH, make_job, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.fake_processing import FakeRenditionWriter
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import PREVIEW_CONTENT, THUMBNAIL_CONTENT, make_page_service
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from pathlib import Path

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project, Scan, Source
    from bookreviver.domain.enums import ColorMode
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.values import RenditionInfo
    from bookreviver.services.pages import PageService

pytestmark = pytest.mark.anyio

PNG_BIT_DEPTH_OFFSET: int = 24
PAGES_DIRECTORY: str = 'pages'
FAILED_AND_NEXT_JOBS: int = 2
LIST_JOBS_PATCH: str = 'bookreviver.adapters.persistence.memory.unit_of_work.InMemoryJobRepository.list_for_project'
WHITE: int = 255
FIRST_SCAN_SIZE: tuple[int, int, float | None] = (100, 400, 100.0)
# Widths, heights and resolutions whose medians are 200, 300 and 200.0
SIZES: list[tuple[int, int, float | None]] = [(100, 500, 100.0), (300, 100, 300.0), (200, 300, 200.0)]


class BookOfScans(NamedTuple):
    """A project whose pages are cut from scans that are cut and stored, each page with its ready base version.

    :ivar project: The project.
    :ivar source: The source of every scan.
    :ivar scans: The scans, whose full images are stored.
    :ivar pages: The pages in book order, one per scan.
    :ivar versions: The ready base versions of the pages.
    """

    project: Project
    source: Source
    scans: list[Scan]
    pages: list[Page]
    versions: list[PageVersion]


class FlakyRenditionWriter(FakeRenditionWriter):
    """A writer of the files of a version that fails the first times it is asked, as a full disk would.

    :ivar attempts: Number of times it was asked.
    """

    def __init__(self, *, fail_first: int) -> None:
        """Fail the first ``fail_first`` calls.

        :param fail_first: Number of calls to fail before one succeeds.
        :type fail_first: int
        """
        super().__init__()
        self.attempts = 0
        self._fail_first = fail_first

    @override
    async def write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Fail, or write the files as the fake writer does.

        :param image: Image to write.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Colour of the image.
        :type color_mode: ColorMode
        :returns: What the fake writer returns.
        :rtype: RenditionInfo
        :raises OSError: For the first calls.
        """
        self.attempts += 1
        if self.attempts <= self._fail_first:
            err_msg = 'No space left on device'
            raise OSError(err_msg)
        return await super().write(image, target_dir, full=full, color_mode=color_mode)


class BrokenQueue(JobQueue):
    """A job queue that cannot take a job."""

    @override
    async def enqueue(self, job: object) -> None:
        """Refuse the job.

        :param job: Job to enqueue.
        :type job: object
        :raises ConnectionError: Always.
        """
        err_msg = 'The broker is down'
        raise ConnectionError(err_msg)


async def _commit_book(
    database: InMemoryDatabase,
    assets: LocalAssetStore,
    owner: Actor,
    sizes: Sequence[tuple[int, int, float | None]] = (FIRST_SCAN_SIZE,),
) -> BookOfScans:
    """Commit a project with a scan of each size, a page of each scan and its ready base version, and store the images.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param assets: Asset store receiving the full image of each scan.
    :type assets: LocalAssetStore
    :param owner: Account owning the project.
    :type owner: Actor
    :param sizes: Width, height and resolution of each scan.
    :type sizes: Sequence[tuple[int, int, float | None]]
    :returns: The stored book.
    :rtype: BookOfScans
    """
    project = make_project(owner_id=owner.account_id)
    source = make_source(project_id=project.id)
    scans = []
    for number, (width, height, dpi) in enumerate(sizes):
        scan = make_scan(source=source, number=number)
        facts = evolve(scan.facts, width_px=width, height_px=height, dpi_x=dpi, dpi_y=dpi)
        scans.append(evolve(scan, facts=facts, renditions=Renditions(ready=True), source_label=f'[{number}]'))
    pages = [make_page(project_id=project.id, order_key=f'a{number}', scan=scan) for number, scan in enumerate(scans)]
    versions = [
        BaseVersions.split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH)
        for page, scan in zip(pages, scans, strict=True)
    ]
    await commit_project(database, project, *pages, sources=[source], scans=scans, versions=versions)
    keys = ProjectKeys(project.id)
    for scan in scans:
        async with assets.writable(keys.scan_rendition(scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(IMAGE)
    return BookOfScans(project=project, source=source, scans=scans, pages=pages, versions=versions)


async def _placeholder_book(
    database: InMemoryDatabase, assets: LocalAssetStore, owner: Actor
) -> tuple[BookOfScans, Page]:
    """Commit a book of one scan page, and a placeholder after it.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param assets: Asset store receiving the full image of the scan.
    :type assets: LocalAssetStore
    :param owner: Account owning the project.
    :type owner: Actor
    :returns: The book and its placeholder.
    :rtype: tuple[BookOfScans, Page]
    """
    book = await _commit_book(database, assets, owner)
    placeholder = evolve(make_page(project_id=book.project.id, order_key='a5'), kind=PageKind.COVER, notes='Missing')
    uow = InMemoryUnitOfWork(database)
    await uow.pages.add(placeholder)
    await uow.commit()
    return book, placeholder


async def _spare_scan(database: InMemoryDatabase, assets: LocalAssetStore, book: BookOfScans) -> Scan:
    """Commit a cut scan of a second source that no page shows, and store its image.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param assets: Asset store receiving the full image.
    :type assets: LocalAssetStore
    :param book: The book whose project gets the source.
    :type book: BookOfScans
    :returns: The scan, with its label ``xii``.
    :rtype: Scan
    """
    source = make_source(project_id=book.project.id, name='cover.jpg', minutes=5)
    scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True), source_label='xii')
    uow = InMemoryUnitOfWork(database)
    await uow.sources.add(source)
    await uow.scans.add(scan)
    await uow.commit()
    async with assets.writable(ProjectKeys(book.project.id).scan_rendition(scan, Rendition.FULL_JPEG)) as path:
        path.write_bytes(IMAGE)
    return scan


def _stored_versions(database: InMemoryDatabase, page: Page) -> list[PageVersion]:
    """Read the committed versions of a page.

    :param database: In-memory database to read.
    :type database: InMemoryDatabase
    :param page: The page.
    :type page: Page
    :returns: Its versions.
    :rtype: list[PageVersion]
    """
    return [version for version in database.tables.page_versions.values() if version.page_id == page.id]


def _job_kinds(database: InMemoryDatabase) -> list[tuple[JobKind, JobState]]:
    """Read the kind and state of every committed job.

    :param database: In-memory database to read.
    :type database: InMemoryDatabase
    :returns: Kind and state of each job.
    :rtype: list[tuple[JobKind, JobState]]
    """
    return [(job.kind, job.state) for job in database.tables.jobs.values()]


class TestAddPlaceholder:
    """Tests for PageService.add() with a placeholder."""

    async def test_adds_a_page_without_an_image_at_the_end_and_queues_no_job(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a placeholder is a page with its fields and no version, after the last page, announced once.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        new = NewPage(origin=NewPageOrigin.PLACEHOLDER, kind=PageKind.TITLE, label='[3]', notes='Lost')

        overview = await fx_service().add(fx_actor, book.project.id, new)

        page = overview.page
        expect(
            (page.origin, page.kind, page.label, page.notes) == (PageOrigin.PLACEHOLDER, PageKind.TITLE, '[3]', 'Lost')
        )
        expect((page.scan_id, page.slot, page.included) == (None, 0, True))
        expect(page.order_key > book.pages[0].order_key)
        expect((overview.position, overview.image_version) == (1, None))
        expect(_stored_versions(fx_database, page) == [])
        expect(_job_kinds(fx_database) == [])
        expect(
            fx_events.published
            == [PagesChanged(project_id=book.project.id, page_ids=[page.id], change=PageChange.ADDED)]
        )
        assert_expectations()

    @pytest.mark.parametrize('side', [Side.BEFORE, Side.AFTER])
    async def test_puts_the_page_next_to_the_anchor(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        side: Side,
    ) -> None:
        """Verify the page lands before or after its anchor, in a book of two pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param side: Side of the first page the new page goes to.
        :type side: Side
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, [FIRST_SCAN_SIZE, FIRST_SCAN_SIZE])
        anchor = PageAnchor(page_id=book.pages[0].id, side=side)
        new = NewPage(origin=NewPageOrigin.PLACEHOLDER, kind=PageKind.COVER, anchor=anchor)

        overview = await fx_service().add(fx_actor, book.project.id, new)

        # The anchor is the first of two pages, so the new page is the first one before it and the second one after it
        assert overview.position == {Side.BEFORE: 0, Side.AFTER: 1}[side]

    async def test_anchor_that_is_not_a_page_of_the_project_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify an unknown anchor and the project of another account both answer not found, and add nothing.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        stranger = await _commit_book(fx_database, fx_asset_store, evolve(fx_actor, account_id=new_account_id()))
        pages_before = len(fx_database.tables.pages)
        lost = NewPage(
            origin=NewPageOrigin.PLACEHOLDER,
            kind=PageKind.TEXT,
            anchor=PageAnchor(page_id=stranger.pages[0].id, side=Side.BEFORE),
        )

        with pytest.raises(NotFoundError):
            await fx_service().add(fx_actor, book.project.id, lost)
        with pytest.raises(NotFoundError):
            await fx_service().add(fx_actor, stranger.project.id, evolve(lost, anchor=None))

        assert len(fx_database.tables.pages) == pages_before


class TestAddBlank:
    """Tests for PageService.add() with a blank leaf."""

    async def test_takes_the_median_size_of_the_scan_pages_and_queues_one_job(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
    ) -> None:
        """Verify width, height and resolution are medians taken apart, in a pending ``pages.blank`` version.

        A second blank leaf added while the first job is still queued waits for the same job.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, SIZES)
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.ENDPAPER)

        first = await fx_service().add(fx_actor, book.project.id, new)
        second = await fx_service().add(fx_actor, book.project.id, new)

        [version] = _stored_versions(fx_database, first.page)
        expect(first.page.origin is PageOrigin.BLANK)
        expect(
            (version.stage, version.processor, version.state) == (Stage.PAGE_ORDER, PAGES_BLANK, VersionState.PENDING)
        )
        expect(PageSize.from_data(version.data) == PageSize(width_px=200, height_px=300, dpi=200.0))
        expect(version.renditions == Renditions(ready=False, full=Rendition.FULL_PNG))
        expect(len(_stored_versions(fx_database, second.page)) == 1)
        expect(_job_kinds(fx_database) == [(JobKind.PREPARE_PAGES, JobState.QUEUED)])
        expect([job.kind for job in fx_queue.enqueued] == [JobKind.PREPARE_PAGES])
        assert_expectations()

    async def test_median_of_an_even_number_of_pages_is_rounded_and_leaves_out_what_is_not_counted(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the median averages the middle pair, and a page kept out and a blank leaf take no part in it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        sizes = [(100, 100, None), (102, 200, None), (9999, 9999, None), (500, 500, None), (300, 300, None)]
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, sizes)
        uow = InMemoryUnitOfWork(fx_database)
        await uow.pages.update(evolve(book.pages[2], included=False))
        await uow.commit()

        overview = await fx_service().add(
            fx_actor, book.project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)
        )
        again = await fx_service().add(
            fx_actor, book.project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)
        )

        [version] = _stored_versions(fx_database, overview.page)
        expect(PageSize.from_data(version.data) == PageSize(width_px=round((102 + 300) / 2), height_px=250, dpi=None))
        expect(
            PageSize.from_data(_stored_versions(fx_database, again.page)[0].data) == PageSize.from_data(version.data)
        )
        assert_expectations()

    async def test_a_given_size_replaces_the_median(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a leaf of a given size has it, and takes no median, so it needs no page with an image.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        size = PageSize(width_px=640, height_px=480, dpi=96.0)

        overview = await fx_service().add(
            fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size)
        )

        [version] = _stored_versions(fx_database, overview.page)
        assert PageSize.from_data(version.data) == size

    async def test_without_a_size_and_without_pages_with_images_conflicts_and_stores_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
    ) -> None:
        """Verify an empty book gives a blank leaf no size to take, so nothing is stored or queued.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project, make_page(project_id=project.id))

        with pytest.raises(ConflictError):
            await fx_service().add(fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK))

        expect(len(fx_database.tables.pages) == 1)
        expect(fx_queue.enqueued == [])
        assert_expectations()

    async def test_a_queue_that_refuses_the_job_does_not_fail_the_request_that_made_the_page(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify the page is committed and answered, the failed job is stored and announced, and the project is free.

        The page exists once the request commits it, so an error would make a client add it a second time. The
        refused job is failed, which leaves the next page able to queue a job that takes this page's version too.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the clock and the queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        events, clock, queue = fx_runtime
        broken = make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, (events, clock, BrokenQueue()))
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)

        first = await broken.add(fx_actor, book.project.id, new)

        failed_events = [
            event.job for event in events.published if isinstance(event, JobChanged) and event.job.state.is_final
        ]
        expect(_job_kinds(fx_database) == [(JobKind.PREPARE_PAGES, JobState.FAILED)])
        expect([(job.state, job.error) for job in failed_events] == [(JobState.FAILED, NOT_QUEUED)])
        expect(first.page.id in fx_database.tables.pages and len(_stored_versions(fx_database, first.page)) == 1)
        second = await fx_service().add(fx_actor, book.project.id, new)
        expect([job.kind for job in queue.enqueued] == [JobKind.PREPARE_PAGES])
        expect(len(_stored_versions(fx_database, second.page)) == 1)
        assert_expectations()

    async def test_a_bound_scan_is_answered_too_when_the_queue_refuses_the_job(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify binding a scan keeps its page and its pending version when the queue refuses the job.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the clock and the queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)
        events, clock, _ = fx_runtime
        broken = make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, (events, clock, BrokenQueue()))

        overview = await broken.attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        expect(overview.page.scan_id == scan.id)
        expect([version.state for version in _stored_versions(fx_database, placeholder)] == [VersionState.PENDING])
        expect(_job_kinds(fx_database) == [(JobKind.PREPARE_PAGES, JobState.FAILED)])
        assert_expectations()


class TestAttachScan:
    """Tests for PageService.attach_scan()."""

    async def test_turns_the_placeholder_into_a_page_of_the_scan_with_a_pending_base_version(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
    ) -> None:
        """Verify the page shows the whole scan, keeps its kind and notes, takes the scan's label, and waits for a job.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)

        overview = await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        page = overview.page
        [version] = _stored_versions(fx_database, page)
        expect((page.id, page.order_key, page.kind, page.notes) == (placeholder.id, 'a5', PageKind.COVER, 'Missing'))
        expect((page.origin, page.scan_id, page.slot, page.label) == (PageOrigin.SCAN, scan.id, 0, 'xii'))
        expect(
            (version.stage, version.processor, version.state) == (Stage.PAGE_SPLIT, SPLIT_NONE, VersionState.PENDING)
        )
        expect(version.renditions == Renditions(ready=False, full=Rendition.FULL_JPEG))
        expect(PageSize.from_data(version.data) == PageSize(width_px=2200, height_px=1561, dpi=None))
        expect([job.kind for job in fx_queue.enqueued] == [JobKind.PREPARE_PAGES])
        assert_expectations()

    async def test_binds_the_scan_when_the_placeholder_is_changed_between_the_read_and_the_write(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verify a printed number written while the scan is bound is kept, and the scan is bound anyway.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the clock and the queue the service reports through.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param monkeypatch: Fixture that restores the patched repository after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)
        uow = InMemoryUnitOfWork(fx_database)
        read = uow.pages.get
        rival_label = 'iv'

        async def get_then_race(page_id: PageId) -> Page:
            """Read a page, then let a rival request commit a new printed number of it.

            :param page_id: Identifier of the page.
            :type page_id: PageId
            :returns: The page as it was before the rival wrote.
            :rtype: Page
            """
            page = await read(page_id)
            rival = InMemoryUnitOfWork(fx_database)
            await rival.pages.update(evolve(await rival.pages.get(placeholder.id), label=rival_label))
            await rival.commit()
            return page

        monkeypatch.setattr(uow.pages, 'get', get_then_race)

        overview = await make_page_service(uow, fx_asset_store, fx_runtime).attach_scan(
            fx_actor, book.project.id, placeholder.id, scan.id
        )

        expect((overview.page.origin, overview.page.scan_id) == (PageOrigin.SCAN, scan.id))
        expect(overview.page.label == rival_label)
        assert_expectations()

    async def test_keeps_the_label_the_placeholder_had(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the scan's own label fills only an empty label.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        uow = InMemoryUnitOfWork(fx_database)
        await uow.pages.update(evolve(placeholder, label='[1]'))
        await uow.commit()
        scan = await _spare_scan(fx_database, fx_asset_store, book)

        overview = await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        assert overview.page.label == '[1]'

    async def test_scan_of_another_project_and_an_unknown_scan_are_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a scan is bound only through the project that holds it, and the placeholder stays as it was.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        elsewhere = await _commit_book(fx_database, fx_asset_store, fx_actor)

        with pytest.raises(NotFoundError):
            await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, elsewhere.scans[0].id)
        with pytest.raises(NotFoundError):
            await fx_service().attach_scan(
                fx_actor, book.project.id, placeholder.id, make_scan(source=book.source, number=9).id
            )

        assert fx_database.tables.pages[placeholder.id] == placeholder

    @pytest.mark.parametrize('origin', [PageOrigin.SCAN, PageOrigin.BLANK])
    async def test_page_that_is_not_a_placeholder_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        origin: PageOrigin,
    ) -> None:
        """Verify a page that has an image already, from a scan or generated, cannot be bound to another scan.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param origin: Origin of the page the scan is bound to.
        :type origin: PageOrigin
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)
        page = book.pages[0]
        if origin is PageOrigin.BLANK:
            page = evolve(make_page(project_id=book.project.id, order_key='a9'), origin=PageOrigin.BLANK)
            uow = InMemoryUnitOfWork(fx_database)
            await uow.pages.add(page)
            await uow.commit()

        with pytest.raises(NotAPlaceholderError):
            await fx_service().attach_scan(fx_actor, book.project.id, page.id, scan.id)

    async def test_scan_another_page_shows_conflicts_naming_the_place_of_that_page(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a scan the import already made a page of is refused without ``take_over``, and nothing changes.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        before = dict(fx_database.tables.pages)

        with pytest.raises(ScanAlreadyInBookError, match='position 1'):
            await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, book.scans[0].id)

        assert fx_database.tables.pages == before

    async def test_take_over_deletes_the_page_of_the_scan_with_its_versions_and_files(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_storage_root: Path,
    ) -> None:
        """Verify the placeholder takes the scan from the page the import made of it, which is removed with its files.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        keys = ProjectKeys(book.project.id)
        async with fx_asset_store.writable(keys.version_rendition(book.versions[0], Rendition.FULL_JPEG)) as path:
            await anyio.Path(path).write_bytes(IMAGE)
        taken_directory = fx_storage_root / keys.page(book.pages[0].id)
        assert taken_directory.is_dir()

        overview = await fx_service().attach_scan(
            fx_actor, book.project.id, placeholder.id, book.scans[0].id, take_over=True
        )

        expect(book.pages[0].id not in fx_database.tables.pages)
        expect(overview.page.scan_id == book.scans[0].id)
        expect(book.versions[0].id not in fx_database.tables.page_versions)
        expect(not taken_directory.exists())
        assert_expectations()

    async def test_take_over_announces_the_removed_page_and_then_the_bound_one(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify the browser learns of the page that went and of the page that took its scan, each once.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)

        await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, book.scans[0].id, take_over=True)

        removed, edited = (event for event in fx_events.published if isinstance(event, PagesChanged))
        expect((removed.change, list(removed.page_ids)) == (PageChange.REMOVED, [book.pages[0].id]))
        expect((edited.change, list(edited.page_ids)) == (PageChange.EDITED, [placeholder.id]))
        assert_expectations()

    async def test_scan_without_renditions_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a scan the import has not cut yet cannot be bound, since its image does not exist to copy.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)
        uow = InMemoryUnitOfWork(fx_database)
        await uow.scans.update(evolve(scan, renditions=Renditions(ready=False)))
        await uow.commit()

        with pytest.raises(ConflictError):
            await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)


class TestDelete:
    """Tests for PageService.delete()."""

    async def test_removes_the_page_its_versions_and_files_and_keeps_the_scan_and_the_source(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_storage_root: Path,
    ) -> None:
        """Verify the page and everything under ``assets/pages/<page_id>`` go, and a scan can be bound again.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, [FIRST_SCAN_SIZE, FIRST_SCAN_SIZE])
        keys = ProjectKeys(book.project.id)
        doomed, kept = book.pages
        for version in book.versions:
            async with fx_asset_store.writable(keys.version_rendition(version, Rendition.FULL_JPEG)) as path:
                await anyio.Path(path).write_bytes(IMAGE)

        await fx_service().delete(fx_actor, book.project.id, doomed.id)

        expect(doomed.id not in fx_database.tables.pages and kept.id in fx_database.tables.pages)
        expect([version.page_id for version in fx_database.tables.page_versions.values()] == [kept.id])
        expect(not (fx_storage_root / keys.page(doomed.id)).exists())
        expect((fx_storage_root / keys.page(kept.id)).is_dir())
        expect(book.scans[0].id in fx_database.tables.scans and book.source.id in fx_database.tables.sources)
        assert_expectations()

    async def test_announces_the_removed_page_once(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a deleted page is published as removed after the commit.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)

        await fx_service().delete(fx_actor, book.project.id, book.pages[0].id)

        assert fx_events.published == [
            PagesChanged(project_id=book.project.id, page_ids=[book.pages[0].id], change=PageChange.REMOVED)
        ]

    async def test_page_of_another_project_and_of_another_account_are_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a page is deleted only through the project that holds it, and only by the owner.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        stranger = await _commit_book(fx_database, fx_asset_store, evolve(fx_actor, account_id=new_account_id()))

        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, book.project.id, stranger.pages[0].id)
        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, stranger.project.id, stranger.pages[0].id)

        assert len(fx_database.tables.pages) == 2


def _white_leaf_facts(path: Path) -> tuple[str, tuple[int, int], bool, int]:
    """Read the mode, the size, the whiteness and the bit depth of the PNG written for a blank leaf.

    :param path: Path of the PNG.
    :type path: Path
    :returns: The Pillow mode, the size in pixels, whether every pixel is white, and the bit depth of the file's header.
    :rtype: tuple[str, tuple[int, int], bool, int]
    """
    with Image.open(path) as image:
        white = image.getextrema() == (WHITE, WHITE)
        return image.mode, image.size, white, path.read_bytes()[PNG_BIT_DEPTH_OFFSET]


class TestPrepareImages:
    """Tests for PageService.prepare_images()."""

    async def test_writes_a_white_one_bit_png_of_the_given_size_and_marks_the_blank_version_ready(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
        fx_storage_root: Path,
    ) -> None:
        """Verify the job leaves a ready ``pages.blank`` version whose full image is a white 1-bit PNG, and its renditions.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        size = PageSize(width_px=64, height_px=48, dpi=150.0)
        overview = await fx_service().add(
            fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.ENDPAPER, size=size)
        )

        await fx_service().prepare_images(fx_queue.enqueued[0].id)

        [version] = _stored_versions(fx_database, overview.page)
        directory = fx_storage_root / ProjectKeys(project.id).version_directory(version)
        expect(version.state is VersionState.READY)
        expect(version.renditions == Renditions(ready=True, full=Rendition.FULL_PNG))
        expect(_white_leaf_facts(directory / Rendition.FULL_PNG) == ('1', (64, 48), True, 1))
        expect((directory / Rendition.PREVIEW).read_bytes() == PREVIEW_CONTENT)
        expect((directory / Rendition.THUMBNAIL).read_bytes() == THUMBNAIL_CONTENT)
        expect((directory / Rendition.TILES / 'info.json').is_file())
        assert_expectations()

    async def test_copies_the_scan_of_a_bound_page_into_its_own_directory(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_storage_root: Path,
    ) -> None:
        """Verify the version of a page bound to a scan holds a byte copy of the scan's full image, and is ready.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)
        await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        [job] = fx_database.tables.jobs.values()
        await fx_service().prepare_images(job.id)

        [version] = _stored_versions(fx_database, placeholder)
        copy = fx_storage_root / ProjectKeys(book.project.id).version_rendition(version, Rendition.FULL_JPEG)
        expect(version.state is VersionState.READY)
        expect(version.renditions == Renditions(ready=True, full=Rendition.FULL_JPEG))
        expect(copy.read_bytes() == IMAGE)
        assert_expectations()

    async def test_announces_each_ready_version_and_ends_the_job_succeeded(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify the viewer is told of every version as it is ready, and the job's progress and state end complete.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        size = PageSize(width_px=8, height_px=8)
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size)
        first = await fx_service().add(fx_actor, project.id, new)
        second = await fx_service().add(fx_actor, project.id, new)

        await fx_service().prepare_images(fx_queue.enqueued[0].id)

        ready = [event.version.page_id for event in fx_events.published if isinstance(event, PageVersionReady)]
        job = fx_database.tables.jobs[fx_queue.enqueued[0].id]
        # The clock is stopped, so the two versions are told apart by their identifiers, which are hashes
        expect(sorted(ready) == sorted([first.page.id, second.page.id]))
        expect((job.state, job.progress.done, job.progress.total) == (JobState.SUCCEEDED, 2, 2))
        expect(job.finished_at is not None and job.error == '')
        expect(
            any(isinstance(event, JobChanged) and event.job.state is JobState.RUNNING for event in fx_events.published)
        )
        assert_expectations()

    async def test_a_failed_version_keeps_its_reason_and_the_next_job_tries_it_again(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify a version the maker failed on is stored as failed, the job ends failed, and a later job makes it.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the recording queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        flaky = FlakyRenditionWriter(fail_first=1)
        queue = fx_runtime[2]

        def service() -> PageService:
            """Build a service whose maker of blank leaves fails once.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime, renditions=flaky)

        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=PageSize(width_px=8, height_px=8))
        first = await service().add(fx_actor, project.id, new)

        await service().prepare_images(queue.enqueued[0].id)

        [failed] = _stored_versions(fx_database, first.page)
        expect(failed.state is VersionState.FAILED)
        expect(
            VersionData.ERROR in failed.data and failed.renditions == Renditions(ready=False, full=Rendition.FULL_PNG)
        )
        expect(fx_database.tables.jobs[queue.enqueued[0].id].state is JobState.FAILED)
        second = await service().add(fx_actor, project.id, new)
        await service().prepare_images(queue.enqueued[1].id)
        [again] = _stored_versions(fx_database, first.page)
        expect(again.state is VersionState.READY)
        expect(VersionData.ERROR not in again.data)
        expect(_stored_versions(fx_database, second.page)[0].state is VersionState.READY)
        expect(fx_database.tables.jobs[queue.enqueued[1].id].state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_page_whose_scan_was_deleted_fails_with_that_reason(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
    ) -> None:
        """Verify a bound page whose source was deleted before the job ran has nothing to copy, and says so.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        """
        book, placeholder = await _placeholder_book(fx_database, fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_database, fx_asset_store, book)
        await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)
        uow = InMemoryUnitOfWork(fx_database)
        await uow.sources.delete(scan.source_id)
        await uow.commit()

        await fx_service().prepare_images(fx_queue.enqueued[0].id)

        [version] = _stored_versions(fx_database, placeholder)
        expect(version.state is VersionState.FAILED)
        expect('deleted' in version.data[VersionData.ERROR])
        assert_expectations()

    async def test_job_cancelled_while_queued_does_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a job that is cancelled before a worker takes it leaves its versions pending.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        size = PageSize(width_px=8, height_px=8)
        overview = await fx_service().add(
            fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size)
        )
        job = fx_queue.enqueued[0]
        uow = InMemoryUnitOfWork(fx_database)
        await uow.jobs.update(evolve(job, state=JobState.CANCELLED))
        await uow.commit()
        published = len(fx_events.published)

        await fx_service().prepare_images(job.id)

        expect(_stored_versions(fx_database, overview.page)[0].state is VersionState.PENDING)
        expect(len(fx_events.published) == published)
        assert_expectations()

    async def test_job_with_nothing_to_prepare_succeeds(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
    ) -> None:
        """Verify a job that finds no pending or failed version ends succeeded with no steps.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        job = evolve(make_job(project_id=project.id), kind=JobKind.PREPARE_PAGES)
        await commit_project(fx_database, project)
        uow = InMemoryUnitOfWork(fx_database)
        await uow.jobs.add(job)
        await uow.commit()

        await fx_service().prepare_images(job.id)

        stored = fx_database.tables.jobs[job.id]
        assert (stored.state, stored.progress.done, stored.progress.total) == (JobState.SUCCEEDED, 0, 0)


class TwoLeaves(NamedTuple):
    """A project with two blank leaves that wait for their images.

    :ivar project: The project.
    :ivar pages: The pages of the leaves, in book order.
    :ivar root: Storage root of the test, under which the files of the pages lie.
    """

    project: Project
    pages: list[Page]
    root: Path


class ActingRenditionWriter(FakeRenditionWriter):
    """A writer that lets something else happen in the middle of the first version, as another request would.

    :ivar attempts: Number of versions it was asked to write.
    """

    def __init__(self, act: Callable[[PageId], Awaitable[None]]) -> None:
        """Run ``act`` while the first version is being written.

        :param act: Coroutine function called with the identifier of the page whose version is being written.
        :type act: Callable[[PageId], Awaitable[None]]
        """
        super().__init__()
        self.attempts = 0
        self._act = act

    @override
    async def write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Act in the middle of the first version, and write the files as the fake writer does.

        :param image: Image to write.
        :type image: Path
        :param target_dir: Directory to create, which lies under the directory of the page the version belongs to.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Colour of the image.
        :type color_mode: ColorMode
        :returns: What the fake writer returns.
        :rtype: RenditionInfo
        """
        self.attempts += 1
        if self.attempts == 1:
            await self._act(PageId(UUID(target_dir.parts[target_dir.parts.index(PAGES_DIRECTORY) + 1])))
        return await super().write(image, target_dir, full=full, color_mode=color_mode)


class FailingBus(RecordingEventBus):
    """An event bus that cannot publish that a version is ready, as a broken connection would not."""

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Refuse a ready version, and record anything else.

        :param event: Event to deliver.
        :type event: DomainEvent
        :raises ConnectionError: For ``PageVersionReady``.
        """
        if isinstance(event, PageVersionReady):
            err_msg = 'The event bus is down'
            raise ConnectionError(err_msg)
        await super().publish(event)


async def _add_leaves(service: Callable[[], PageService], actor: Actor, project: Project, count: int) -> list[Page]:
    """Add blank leaves of a size given, each in a request of its own, and return their pages.

    :param service: Function building the service for one request.
    :type service: Callable[[], PageService]
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to add the leaves to.
    :type project: Project
    :param count: Number of leaves.
    :type count: int
    :returns: The pages of the leaves, in the order they were added.
    :rtype: list[Page]
    """
    new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=PageSize(width_px=8, height_px=8))
    return [(await service().add(actor, project.id, new)).page for _ in range(count)]


class TestPrepareImagesWhileTheBookChanges:
    """Tests for PageService.prepare_images() when pages are deleted or added while the job runs."""

    @pytest.fixture
    async def fx_leaves(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_storage_root: Path,
    ) -> TwoLeaves:
        """Commit a project with two blank leaves, each queued for its image.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account owning the project.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the recording queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        :returns: The project, its two pages and the storage root.
        :rtype: TwoLeaves
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        pages = await _add_leaves(
            lambda: make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime),
            fx_actor,
            project,
            2,
        )
        return TwoLeaves(project=project, pages=pages, root=fx_storage_root)

    @pytest.mark.parametrize('victim', ['written', 'other'])
    async def test_a_page_deleted_while_the_job_runs_is_nothing_to_make_and_not_a_failure(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_leaves: TwoLeaves,
        victim: str,
    ) -> None:
        """Verify the job ends succeeded with the other page ready and no file left for the deleted one.

        The page is deleted and committed by another transaction while the first leaf is written. In one case it is
        the page being written, whose version is gone when the job stores it, and in the other the page that comes
        next, which is gone when the job reaches it.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the recording queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_leaves: The project and its two leaves.
        :type fx_leaves: TwoLeaves
        :param victim: Whether the page deleted is the one being written or the other one.
        :type victim: str
        """
        events, _, queue = fx_runtime
        project, pages, root = fx_leaves
        deleted: list[PageId] = []

        async def delete_in_another_transaction(written: PageId) -> None:
            """Delete a page of the book and commit, as another request would.

            :param written: The page whose leaf the job is writing.
            :type written: PageId
            """
            page_id = written if victim == 'written' else next(page.id for page in pages if page.id != written)
            other = InMemoryUnitOfWork(fx_database)
            await other.pages.delete(page_id)
            await other.commit()
            deleted.append(page_id)

        service = make_page_service(
            InMemoryUnitOfWork(fx_database),
            fx_asset_store,
            fx_runtime,
            renditions=ActingRenditionWriter(delete_in_another_transaction),
        )

        await service.prepare_images(queue.enqueued[0].id)

        [gone] = deleted
        [kept] = [page for page in pages if page.id != gone]
        job = fx_database.tables.jobs[queue.enqueued[0].id]
        expect((job.state, job.error, job.progress.done, job.progress.total) == (JobState.SUCCEEDED, '', 2, 2))
        expect([version.state for version in _stored_versions(fx_database, kept)] == [VersionState.READY])
        expect(_stored_versions(fx_database, next(page for page in pages if page.id == gone)) == [])
        expect(
            [event.version.page_id for event in events.published if isinstance(event, PageVersionReady)] == [kept.id]
        )
        expect(not (root / ProjectKeys(project.id).page(gone)).exists())
        assert_expectations()

    async def test_unexpected_error_ends_the_job_failed_so_the_project_can_queue_another(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify an error nobody expected ends the job as failed with its reason, and frees the project.

        A job left running would stop every later page from queueing an image job.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the recording queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        _, clock, queue = fx_runtime
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        def service(bus: RecordingEventBus) -> PageService:
            """Build a service that reports to the given bus.

            :param bus: Event bus of the service.
            :type bus: RecordingEventBus
            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, (bus, clock, queue))

        [leaf] = await _add_leaves(lambda: service(RecordingEventBus()), fx_actor, project, 1)

        await service(FailingBus()).prepare_images(queue.enqueued[0].id)

        job = fx_database.tables.jobs[queue.enqueued[0].id]
        expect((job.state, job.error) == (JobState.FAILED, UNEXPECTED_FAILURE))
        expect(job.finished_at is not None)
        await _add_leaves(lambda: service(RecordingEventBus()), fx_actor, project, 1)
        expect(len(queue.enqueued) == FAILED_AND_NEXT_JOBS)
        expect(leaf.id in fx_database.tables.pages)
        assert_expectations()

    async def test_a_page_added_while_a_job_runs_gets_no_second_job_and_a_follow_up_when_it_ends(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify one job runs at a time, and the version committed meanwhile is not missed.

        A leaf is added in another request while the first job writes its leaf. That request finds the job running
        and queues none, and the job, which had read its versions before, queues one more when it ends.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the recording queue of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        queue = fx_runtime[2]
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        def request_service() -> PageService:
            """Build the service of a new request.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime)

        [first] = await _add_leaves(request_service, fx_actor, project, 1)
        queued_while_running: list[int] = []
        added: list[Page] = []

        async def add_a_leaf(_: PageId) -> None:
            """Add a leaf in a request of its own, and note how many jobs are queued afterwards.

            :param _: The page whose leaf the job is writing.
            :type _: PageId
            """
            added.extend(await _add_leaves(request_service, fx_actor, project, 1))
            queued_while_running.append(len(queue.enqueued))

        service = make_page_service(
            InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime, renditions=ActingRenditionWriter(add_a_leaf)
        )

        await service.prepare_images(queue.enqueued[0].id)

        expect(queued_while_running == [1])
        expect([job.kind for job in queue.enqueued] == [JobKind.PREPARE_PAGES, JobKind.PREPARE_PAGES])
        await make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime).prepare_images(
            queue.enqueued[1].id
        )
        expect(
            [_stored_versions(fx_database, page)[0].state for page in (first, *added)]
            == [VersionState.READY, VersionState.READY]
        )
        expect({job.state for job in fx_database.tables.jobs.values()} == {JobState.SUCCEEDED})
        assert_expectations()

    @patch(LIST_JOBS_PATCH, new_callable=AsyncMock)
    async def test_two_requests_that_both_pass_the_check_store_one_job(
        self,
        list_jobs: AsyncMock,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
    ) -> None:
        """Verify the request that loses the race finds the job of the other, and neither fails nor queues a second.

        The check for an active job is made to find none, as it does for a request that reads before the other
        commits, so only the unique key of the jobs can stop the second insert.

        :param list_jobs: Patched listing of a project's jobs, which finds none.
        :type list_jobs: AsyncMock
        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        """
        list_jobs.return_value = []
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        pages = await _add_leaves(fx_service, fx_actor, project, 2)

        expect(_job_kinds(fx_database) == [(JobKind.PREPARE_PAGES, JobState.QUEUED)])
        expect(len(fx_queue.enqueued) == 1)
        expect(all(len(_stored_versions(fx_database, page)) == 1 for page in pages))
        assert_expectations()

    async def test_second_leaf_while_a_job_is_queued_queues_no_second_job(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a job queued or running is waited for by later pages, which is what the unique key asks of them.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        await _add_leaves(fx_service, fx_actor, project, 3)

        assert _job_kinds(fx_database) == [(JobKind.PREPARE_PAGES, JobState.QUEUED)]


class TestAddBlankWithoutSizedPages:
    """Tests for the size of a blank leaf in a book whose pages give none."""

    async def test_book_whose_scan_pages_are_all_kept_out_says_it_is_the_recorded_size_that_is_missing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the refusal names what the median needs, and does not claim that the pages have no image.

        The pages have images and sizes, but they are kept out of the book, so none is counted. A size given still
        makes the leaf.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, SIZES)
        uow = InMemoryUnitOfWork(fx_database)
        for page in book.pages:
            await uow.pages.update(evolve(page, included=False))
        await uow.commit()
        blank = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)

        with pytest.raises(ConflictError, match='cut from a scan and part of the book has a recorded size') as refused:
            await fx_service().add(fx_actor, book.project.id, blank)
        made = await fx_service().add(fx_actor, book.project.id, evolve(blank, size=PageSize(width_px=8, height_px=9)))

        expect('has an image' not in str(refused.value))
        expect(
            PageSize.from_data(_stored_versions(fx_database, made.page)[0].data) == PageSize(width_px=8, height_px=9)
        )
        assert_expectations()


class CountingOrderKeys(FractionalOrderKeys):
    """The fractional order keys, which count how many times keys were asked for.

    :ivar calls: Number of calls of ``spread``.
    """

    def __init__(self) -> None:
        """Start counting from zero."""
        self.calls = 0

    @override
    def spread(self, *, lower: str | None, upper: str | None, count: int) -> Sequence[str]:
        """Count the call and make the keys as the fractional builder does.

        :param lower: Key before the new positions, or None at the start of the book.
        :type lower: str | None
        :param upper: Key after the new positions, or None at the end of the book.
        :type upper: str | None
        :param count: Number of keys.
        :type count: int
        :returns: The keys in ascending order.
        :rtype: Sequence[str]
        """
        self.calls += 1
        return super().spread(lower=lower, upper=upper, count=count)


def _labels_in_book_order(database: InMemoryDatabase) -> list[str]:
    """Read the labels of the committed pages in the order of the book.

    :param database: In-memory database to read.
    :type database: InMemoryDatabase
    :returns: The label of each page, in order key order.
    :rtype: list[str]
    """
    return [page.label for page in sorted(database.tables.pages.values(), key=lambda page: page.order_key)]


def _placeholder(label: str, anchor: PageAnchor | None = None) -> NewPage:
    """Build a placeholder for a batch.

    :param label: Printed number of the page.
    :type label: str
    :param anchor: Place of the page, or None for the place after the page before it in the list.
    :type anchor: PageAnchor | None
    :returns: The new page.
    :rtype: NewPage
    """
    return NewPage(origin=NewPageOrigin.PLACEHOLDER, kind=PageKind.TEXT, label=label, anchor=anchor)


class TestAddMany:
    """Tests for PageService.add_many()."""

    async def test_adds_the_whole_list_at_the_end_in_order_with_one_event_and_one_key_call(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify pages without a place stand at the end in the order listed, announced once, with one key call.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the recording queue.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        keys = CountingOrderKeys()
        service = make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime, order_keys=keys)

        added = await service.add_many(fx_actor, book.project.id, [_placeholder(str(n)) for n in range(1, 6)])

        expect([overview.position for overview in added] == [1, 2, 3, 4, 5])
        expect(_labels_in_book_order(fx_database) == ['', '1', '2', '3', '4', '5'])
        expect(keys.calls == 1)
        expect(
            fx_events.published
            == [
                PagesChanged(
                    project_id=book.project.id,
                    page_ids=[overview.page.id for overview in added],
                    change=PageChange.ADDED,
                )
            ]
        )
        assert_expectations()

    async def test_pages_without_a_place_follow_the_page_before_them_and_each_anchor_is_one_place(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        fx_actor: Actor,
    ) -> None:
        """Verify a run starts at its anchor, continues after the page before it, and keeps the order listed.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the recording queue.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, [FIRST_SCAN_SIZE] * 3)
        first, _, last = book.pages
        keys = CountingOrderKeys()
        service = make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime, order_keys=keys)
        before_last = PageAnchor(page_id=last.id, side=Side.BEFORE)
        after_first = PageAnchor(page_id=first.id, side=Side.AFTER)
        # The labels of the scan pages are empty, so the new ones tell the places apart
        new = [
            _placeholder('a', before_last),
            _placeholder('b'),
            _placeholder('c', after_first),
            _placeholder('d'),
            _placeholder('e', before_last),
        ]

        added = await service.add_many(fx_actor, book.project.id, new)

        expect(_labels_in_book_order(fx_database) == ['', 'c', 'd', '', 'a', 'b', 'e', ''])
        expect([overview.page.label for overview in added] == ['a', 'b', 'c', 'd', 'e'])
        expect([overview.position for overview in added] == [4, 5, 1, 2, 6])
        expect(keys.calls == 2)
        assert_expectations()

    async def test_blank_leaves_get_one_pending_version_each_and_one_job(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_queue: RecordingJobQueue,
    ) -> None:
        """Verify blank leaves take the median size once, a given size is kept, and one job is queued for all.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_queue: Recording job queue.
        :type fx_queue: RecordingJobQueue
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor, SIZES)
        sized = PageSize(width_px=8, height_px=9)
        new = [
            NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK),
            _placeholder('p'),
            NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=sized),
        ]

        added = await fx_service().add_many(fx_actor, book.project.id, new)

        sizes = [PageSize.from_data(_stored_versions(fx_database, added[i].page)[0].data) for i in (0, 2)]
        expect(sizes == [PageSize(width_px=200, height_px=300, dpi=200.0), sized])
        expect(_stored_versions(fx_database, added[1].page) == [])
        expect([job.kind for job in fx_queue.enqueued] == [JobKind.PREPARE_PAGES])
        assert_expectations()

    async def test_a_bad_anchor_in_the_middle_names_its_index_and_stores_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify an anchor that is no page of the book refuses the whole list, and no page or event is left.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        lost = PageAnchor(page_id=PageId(UUID(int=7)), side=Side.AFTER)
        new = [_placeholder('1'), _placeholder('2'), _placeholder('3', lost)]

        with pytest.raises(ConflictError, match='index 2 of the list'):
            await fx_service().add_many(fx_actor, book.project.id, new)

        expect(len(fx_database.tables.pages) == len(book.pages))
        expect(fx_events.published == [])
        assert_expectations()

    async def test_a_blank_leaf_without_a_size_in_a_book_without_images_names_its_index(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the first leaf that lacks a size is named, and the placeholders before it are not stored.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        new = [_placeholder('1'), NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)]

        with pytest.raises(ConflictError, match='index 1 of the list'):
            await fx_service().add_many(fx_actor, project.id, new)

        assert fx_database.tables.pages == {}

    async def test_refuses_more_pages_than_the_limit_and_a_project_of_another_account(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a list over ``MAX_PAGES_PER_BATCH`` is a conflict and a stranger's project is not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        stranger = await _commit_book(fx_database, fx_asset_store, evolve(fx_actor, account_id=new_account_id()))
        too_many = [_placeholder('x')] * (fx_service().MAX_PAGES_PER_BATCH + 1)

        with pytest.raises(ConflictError):
            await fx_service().add_many(fx_actor, book.project.id, too_many)
        with pytest.raises(NotFoundError):
            await fx_service().add_many(fx_actor, stranger.project.id, [_placeholder('1')])

        assert len(fx_database.tables.pages) == len(book.pages) + len(stranger.pages)

    async def test_a_thousand_pages_are_one_transaction(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_actor: Actor,
    ) -> None:
        """Verify the largest list is added whole and in order.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        labels = [str(number) for number in range(fx_service().MAX_PAGES_PER_BATCH)]

        await fx_service().add_many(fx_actor, project.id, [_placeholder(label) for label in labels])

        assert _labels_in_book_order(fx_database) == labels
