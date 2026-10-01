"""Tests for the use cases that add and delete pages without a scan, bind scans, and write their images."""

from typing import TYPE_CHECKING, NamedTuple, override

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

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
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import JobChanged, PagesChanged, PageVersionReady
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPage, PageAnchor, PageSize, Renditions
from bookreviver.ports.imaging import BlankPageMaker
from bookreviver.ports.runtime import JobQueue
from bookreviver.services.base_versions import PAGES_BLANK, SPLIT_NONE, BaseVersions
from tests.helpers.books import IMAGE
from tests.helpers.builders import EPOCH, make_job, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.page_services import PREVIEW_CONTENT, THUMBNAIL_CONTENT, make_page_service
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project, Scan, Source
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

PNG_BIT_DEPTH_OFFSET: int = 24
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


class FlakyBlankMaker(BlankPageMaker):
    """A maker of blank leaves that fails the first time it is asked, as a full disk would.

    :ivar calls: Number of times it was asked.
    """

    def __init__(self, *, fail_first: int) -> None:
        """Fail the first ``fail_first`` calls.

        :param fail_first: Number of calls to fail before one succeeds.
        :type fail_first: int
        """
        self.calls = 0
        self._fail_first = fail_first

    @override
    async def make(self, target: Path, *, width_px: int, height_px: int, dpi: float | None) -> None:
        """Fail, or write a token file.

        :param target: Path to write at.
        :type target: Path
        :param width_px: Ignored.
        :type width_px: int
        :param height_px: Ignored.
        :type height_px: int
        :param dpi: Ignored.
        :type dpi: float | None
        :raises OSError: For the first calls.
        """
        self.calls += 1
        if self.calls <= self._fail_first:
            err_msg = 'No space left on device'
            raise OSError(err_msg)
        await anyio.Path(target).write_bytes(b'png')


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
        expect((overview.position, overview.base_version) == (1, None))
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

    async def test_job_the_queue_refuses_is_failed_and_the_error_surfaces(
        self,
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    ) -> None:
        """Verify a job that could not be queued does not stay queued, which would stop every later page queueing one.

        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus and the clock of the test, with the queue it replaces.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        """
        book = await _commit_book(fx_database, fx_asset_store, fx_actor)
        events, clock, _ = fx_runtime
        service = make_page_service(fx_database, fx_asset_store, (events, clock, BrokenQueue()))

        with pytest.raises(ConnectionError):
            await service.add(fx_actor, book.project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK))

        expect(_job_kinds(fx_database) == [(JobKind.PREPARE_PAGES, JobState.FAILED)])
        expect(len(fx_database.tables.pages) == len(book.pages) + 1)
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

        with pytest.raises(ConflictError):
            await fx_service().attach_scan(fx_actor, book.project.id, page.id, scan.id)

    async def test_scan_another_page_shows_conflicts_naming_that_page(
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

        with pytest.raises(ConflictError, match=str(book.pages[0].id)):
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
        flaky = FlakyBlankMaker(fail_first=1)
        queue = fx_runtime[2]

        def service() -> PageService:
            """Build a service whose maker of blank leaves fails once.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(fx_database, fx_asset_store, fx_runtime, blank_maker=flaky)

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
