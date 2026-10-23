"""Tests for the use cases that add and delete pages without a scan, bind scans, and write their images."""

from typing import TYPE_CHECKING, NamedTuple, override
from uuid import UUID

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.enums import (
    BookResource,
    JobKind,
    JobState,
    NewPageOrigin,
    PageKind,
    PageOrigin,
    Rendition,
    Side,
    Stage,
    VersionData,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotAPlaceholderError, NotFoundError, ScanAlreadyInBookError
from bookreviver.domain.events import JobChanged, PageVersionReady
from bookreviver.domain.ids import PageId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import JobEnd, NewPage, PageAnchor, PageSize, Renditions
from bookreviver.services.base_versions import PAGES_BLANK, SPLIT_NONE, BaseVersions
from bookreviver.services.pages import PAGES_FAILED, UNEXPECTED_FAILURE
from bookreviver.services.projects import book_pages
from tests.helpers.acting_renditions import ActingRenditionWriter
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.books import IMAGE
from tests.helpers.builders import EPOCH, make_job, make_page, make_project, make_scan, make_source
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fake_processing import FakeRenditionWriter
from tests.helpers.fakes_imports import stored_book
from tests.helpers.page_services import PREVIEW_CONTENT, THUMBNAIL_CONTENT, make_page_service
from tests.helpers.seeding import commit_account, store_project

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Job, Page, PageOverview, PageVersion, Project, Scan, Source
    from bookreviver.domain.enums import ColorMode
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import RenditionInfo
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.pages import PageService
    from tests.helpers.clock import FixedClock
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

PNG_BIT_DEPTH_OFFSET: int = 24
FAILED_AND_NEXT_JOBS: int = 2
WHITE: int = 255
RIVAL_LABEL: str = 'iv'
PLACEHOLDER_ORDER_KEY: str = 'a5'
PLACEHOLDER_NOTES: str = 'Missing'
SPARE_SCAN_LABEL: str = 'xii'
LOST_NOTES: str = 'Lost'
THIRD_LABEL: str = '[3]'
FIRST_LABEL: str = '[1]'
WRITTEN_VICTIM: str = 'written'
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


async def _commit_book(
    uow: UnitOfWork,
    assets: LocalAssetStore,
    owner: Actor,
    sizes: Sequence[tuple[int, int, float | None]] = (FIRST_SCAN_SIZE,),
) -> BookOfScans:
    """Commit a project with a scan of each size, a page of each scan and its ready base version, and store the images.

    :param uow: Unit of work to commit through, over a database that stores the account of the owner.
    :type uow: UnitOfWork
    :param assets: Asset store receiving the full image of each scan.
    :type assets: LocalAssetStore
    :param owner: Account owning the project.
    :type owner: Actor
    :param sizes: Width, height and resolution of each scan.
    :type sizes: Sequence[tuple[int, int, float | None]]
    :returns: The stored book.
    :rtype: BookOfScans
    """
    project = make_project()
    source = make_source()
    scans = []
    for number, (width, height, dpi) in enumerate(sizes):
        scan = make_scan(source=source, number=number)
        facts = evolve(scan.facts, width_px=width, height_px=height, dpi_x=dpi, dpi_y=dpi)
        scans.append(evolve(scan, facts=facts, renditions=Renditions(ready=True), source_label=f'[{number}]'))
    pages = [make_page(order_key=f'a{number}', scan=scan) for number, scan in enumerate(scans)]
    versions = [
        BaseVersions.split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH)
        for page, scan in zip(pages, scans, strict=True)
    ]
    await store_project(uow, project, *pages, owner=owner.account_id, sources=[source], scans=scans, versions=versions)
    keys = ProjectKeys(project.id)
    for scan in scans:
        async with assets.writable(keys.scan_rendition(scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(IMAGE)
    return BookOfScans(project=project, source=source, scans=scans, pages=pages, versions=versions)


async def _placeholder_book(uow: UnitOfWork, assets: LocalAssetStore, owner: Actor) -> tuple[BookOfScans, Page]:
    """Commit a book of one scan page, and a placeholder after it.

    :param uow: Unit of work to commit through, over a database that stores the account of the owner.
    :type uow: UnitOfWork
    :param assets: Asset store receiving the full image of the scan.
    :type assets: LocalAssetStore
    :param owner: Account owning the project.
    :type owner: Actor
    :returns: The book and its placeholder.
    :rtype: tuple[BookOfScans, Page]
    """
    book = await _commit_book(uow, assets, owner)
    page = make_page(order_key=PLACEHOLDER_ORDER_KEY)
    placeholder = evolve(page, kind=PageKind.COVER, notes=PLACEHOLDER_NOTES)
    await uow.change_book(book.project.id, lambda repos, _project: repos.pages.add(placeholder))
    return book, placeholder


async def _spare_scan(uow: UnitOfWork, assets: LocalAssetStore, book: BookOfScans) -> Scan:
    """Commit a cut scan of a second source that no page shows, and store its image.

    :param uow: Unit of work to commit through.
    :type uow: UnitOfWork
    :param assets: Asset store receiving the full image.
    :type assets: LocalAssetStore
    :param book: The book whose project gets the source.
    :type book: BookOfScans
    :returns: The scan, with its label ``xii``.
    :rtype: Scan
    """
    source = make_source(name='cover.jpg', minutes=5)
    scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True), source_label=SPARE_SCAN_LABEL)

    def add_scan(repos: BookRepositories, _project: Project) -> None:
        """Add the source and its scan.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: The project as the change finds it, which the work does not need.
        :type _project: Project
        """
        repos.sources.add(source)
        repos.scans.add(scan)

    await uow.change_book(book.project.id, add_scan)
    async with assets.writable(ProjectKeys(book.project.id).scan_rendition(scan, Rendition.FULL_JPEG)) as path:
        path.write_bytes(IMAGE)
    return scan


async def _stored_versions(uow: UnitOfWork, project_id: ProjectId, page: Page) -> list[PageVersion]:
    """Read the committed versions of a page.

    :param uow: Unit of work to read through.
    :type uow: UnitOfWork
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param page: The page.
    :type page: Page
    :returns: Its versions, the earliest first.
    :rtype: list[PageVersion]
    """
    return list(await uow.read_book(project_id, lambda repos: repos.page_versions.list_for_page(page.id)))


async def _stored_pages(uow: UnitOfWork, *projects: Project) -> list[Page]:
    """Read the committed pages of projects.

    :param uow: Unit of work to read through.
    :type uow: UnitOfWork
    :param projects: Projects whose pages are read.
    :type projects: Project
    :returns: The pages of each project in book order, one project after the other.
    :rtype: list[Page]
    """

    async def pages_of(project: Project) -> list[Page]:
        """Read the pages of one project in book order.

        :param project: The project.
        :type project: Project
        :returns: Its pages.
        :rtype: list[Page]
        """
        return await uow.read_book(project.id, lambda repos: book_pages(repos.pages))

    return [page for project in projects for page in await pages_of(project)]


@pytest.fixture
async def fx_stranger(fx_database: SqlDatabase, fx_actor: Actor) -> Actor:
    """Commit the account of a user who acts nowhere in the test.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_actor: Account the service acts for.
    :type fx_actor: Actor
    :returns: The actor of the other account.
    :rtype: Actor
    """
    return evolve(fx_actor, account_id=await commit_account(fx_database))


def _job_kinds(board: FakeJobBoard) -> list[tuple[JobKind, JobState]]:
    """Read the kind and the state, as the board holds it now, of every job put on the board.

    :param board: Board that records the jobs put on it.
    :type board: FakeJobBoard
    :returns: Kind and state of each job, in the order the jobs were put.
    :rtype: list[tuple[JobKind, JobState]]
    """
    return [(job.kind, board.get(job.id).state) for job in board.put_jobs]


def _prepare_jobs(board: FakeJobBoard) -> list[Job]:
    """Read the ``prepare-pages`` jobs put on the board, as the board holds them now.

    :param board: Board that records the jobs put on it.
    :type board: FakeJobBoard
    :returns: The jobs, in the order they were put.
    :rtype: list[Job]
    """
    return [board.get(job.id) for job in board.put_jobs if job.kind is JobKind.PREPARE_PAGES]


def _board_that_runs(service: Callable[[], PageService], events: RecordingEventBus, clock: FixedClock) -> FakeJobBoard:
    """Build a board that runs a ``prepare-pages`` job through the page service a test built, and ends any other at once.

    The service puts its jobs on a board, and the board runs them through the service, so a test builds the service
    over the board this function returns, which the function it passes in reaches when a job runs.

    :param service: Function building the page service that runs a job.
    :type service: Callable[[], PageService]
    :param events: Bus the board announces its jobs to.
    :type events: RecordingEventBus
    :param clock: Clock stamping the jobs.
    :type clock: FixedClock
    :returns: The board, which runs its queues when a test calls ``run_queued``.
    :rtype: FakeJobBoard
    """

    async def run(job: Job, control: JobControl) -> JobEnd:
        """Run a job the way the application would, over the page service of the test.

        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        """
        if job.kind is not JobKind.PREPARE_PAGES:
            return JobEnd(state=JobState.SUCCEEDED)
        return await service().prepare_images(job, control)

    return FakeJobBoard(runner=run, publisher=events, clock=clock)


class TestAddPlaceholder:
    """Tests for PageService.add() with a placeholder."""

    async def test_adds_a_page_without_an_image_at_the_end_and_queues_no_job(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
    ) -> None:
        """Verify a placeholder is a page with its fields and no version, after the last page.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the job board of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        """
        _, _, board = fx_runtime
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        new = NewPage(origin=NewPageOrigin.PLACEHOLDER, kind=PageKind.TITLE, label=THIRD_LABEL, notes=LOST_NOTES)

        overview = await fx_service().add(fx_actor, book.project.id, new)

        page = overview.page
        expect(
            (page.origin, page.kind, page.manual_label, page.notes)
            == (PageOrigin.PLACEHOLDER, PageKind.TITLE, THIRD_LABEL, LOST_NOTES)
        )
        expect((page.scan_id, page.slot, page.included) == (None, 0, True))
        expect(page.order_key > book.pages[0].order_key)
        expect((overview.position, overview.image_version) == (1, None))
        expect(await _stored_versions(fx_uow(), book.project.id, page) == [])
        expect(_job_kinds(board) == [])
        assert_expectations()

    @pytest.mark.parametrize('side', [Side.BEFORE, Side.AFTER])
    async def test_puts_the_page_next_to_the_anchor(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        side: Side,
    ) -> None:
        """Verify the page lands before or after its anchor, in a book of two pages.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param side: Side of the first page the new page goes to.
        :type side: Side
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, [FIRST_SCAN_SIZE, FIRST_SCAN_SIZE])
        anchor = PageAnchor(page_id=book.pages[0].id, side=side)
        new = NewPage(origin=NewPageOrigin.PLACEHOLDER, kind=PageKind.COVER, anchor=anchor)

        overview = await fx_service().add(fx_actor, book.project.id, new)

        # The anchor is the first of two pages, so the new page is the first one before it and the second one after it
        assert overview.position == {Side.BEFORE: 0, Side.AFTER: 1}[side]

    async def test_anchor_that_is_not_a_page_of_the_project_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_stranger: Actor,
    ) -> None:
        """Verify an unknown anchor and the project of another account both answer not found, and add nothing.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_stranger: Account of another user.
        :type fx_stranger: Actor
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        stranger = await _commit_book(fx_uow(), fx_asset_store, fx_stranger)
        pages_before = await _stored_pages(fx_uow(), book.project, stranger.project)
        lost = NewPage(
            origin=NewPageOrigin.PLACEHOLDER,
            kind=PageKind.TEXT,
            anchor=PageAnchor(page_id=stranger.pages[0].id, side=Side.BEFORE),
        )

        with pytest.raises(NotFoundError):
            await fx_service().add(fx_actor, book.project.id, lost)
        with pytest.raises(NotFoundError):
            await fx_service().add(fx_actor, stranger.project.id, evolve(lost, anchor=None))

        assert await _stored_pages(fx_uow(), book.project, stranger.project) == pages_before


class TestAddBlank:
    """Tests for PageService.add() with a blank leaf."""

    async def test_takes_the_median_size_of_the_scan_pages_and_queues_one_job(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
    ) -> None:
        """Verify width, height and resolution are medians taken apart, in a pending ``pages.blank`` version.

        A second blank leaf added while the first job is still queued waits for the same job.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, SIZES)
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.ENDPAPER)

        first = await fx_service().add(fx_actor, book.project.id, new)
        second = await fx_service().add(fx_actor, book.project.id, new)

        [version] = await _stored_versions(fx_uow(), book.project.id, first.page)
        expect(first.page.origin is PageOrigin.BLANK)
        expect(
            (version.stage, version.processor, version.state) == (Stage.PAGE_ORDER, PAGES_BLANK, VersionState.PENDING)
        )
        expect(PageSize.from_data(version.data) == PageSize(width_px=200, height_px=300, dpi=200.0))
        expect(version.renditions == Renditions(ready=False, full=Rendition.FULL_PNG))
        expect(len(await _stored_versions(fx_uow(), book.project.id, second.page)) == 1)
        expect(_job_kinds(fx_board) == [(JobKind.PREPARE_PAGES, JobState.QUEUED)])
        assert_expectations()

    async def test_median_of_an_even_number_of_pages_is_rounded_and_leaves_out_what_is_not_counted(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the median averages the middle pair, and a page kept out and a blank leaf take no part in it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        sizes = [(100, 100, None), (102, 200, None), (9999, 9999, None), (500, 500, None), (300, 300, None)]
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, sizes)
        await fx_uow().change_book(
            book.project.id, lambda repos, _project: repos.pages.update(evolve(book.pages[2], included=False))
        )

        overview = await fx_service().add(
            fx_actor, book.project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)
        )
        again = await fx_service().add(
            fx_actor, book.project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)
        )

        [version] = await _stored_versions(fx_uow(), book.project.id, overview.page)
        expect(PageSize.from_data(version.data) == PageSize(width_px=round((102 + 300) / 2), height_px=250, dpi=None))
        again_version = (await _stored_versions(fx_uow(), book.project.id, again.page))[0]
        expect(PageSize.from_data(again_version.data) == PageSize.from_data(version.data))
        assert_expectations()

    async def test_a_given_size_replaces_the_median(
        self, fx_service: Callable[[], PageService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a leaf of a given size has it, and takes no median, so it needs no page with an image.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        size = PageSize(width_px=640, height_px=480, dpi=96.0)

        overview = await fx_service().add(
            fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size)
        )

        [version] = await _stored_versions(fx_uow(), project.id, overview.page)
        assert PageSize.from_data(version.data) == size

    async def test_without_a_size_and_without_pages_with_images_conflicts_and_stores_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
    ) -> None:
        """Verify an empty book gives a blank leaf no size to take, so nothing is stored or queued.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        project = make_project()
        await store_project(fx_uow(), project, make_page(), owner=fx_actor.account_id)

        with pytest.raises(ConflictError):
            await fx_service().add(fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK))

        expect(len(await _stored_pages(fx_uow(), project)) == 1)
        expect(fx_board.put_jobs == [])
        assert_expectations()


class TestAttachScan:
    """Tests for PageService.attach_scan()."""

    async def test_turns_the_placeholder_into_a_page_of_the_scan_with_a_pending_base_version(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
    ) -> None:
        """Verify the page shows the whole scan, keeps its kind and notes, takes the scan's label, and waits for a job.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)

        overview = await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        page = overview.page
        [version] = await _stored_versions(fx_uow(), book.project.id, page)
        expect(
            (page.id, page.order_key, page.kind, page.notes)
            == (placeholder.id, PLACEHOLDER_ORDER_KEY, PageKind.COVER, PLACEHOLDER_NOTES)
        )
        expect(
            (page.origin, page.scan_id, page.slot, page.manual_label) == (PageOrigin.SCAN, scan.id, 0, SPARE_SCAN_LABEL)
        )
        expect(
            (version.stage, version.processor, version.state) == (Stage.PAGE_SPLIT, SPLIT_NONE, VersionState.PENDING)
        )
        expect(version.renditions == Renditions(ready=False, full=Rendition.FULL_JPEG))
        expect(PageSize.from_data(version.data) == PageSize(width_px=2200, height_px=1561, dpi=None))
        expect([job.kind for job in fx_board.put_jobs] == [JobKind.PREPARE_PAGES])
        assert_expectations()

    async def test_binds_the_scan_after_a_printed_number_written_before_it_in_the_queue_of_the_book(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
        fx_actor: Actor,
    ) -> None:
        """Verify a printed number written by a change asked for before the scan is bound is kept, and the scan bound.

        The book is held, the change of the number asks for it first and the binding second, so the binding waits and
        reads the placeholder after the number was written, which is proved with ``wait_all_tasks_blocked``, never
        with a delay.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the clock and the job board the service reports through.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)
        binding = make_page_service(fx_uow(), fx_asset_store, fx_runtime)
        bound: list[PageOverview] = []

        def write_the_number(repos: BookRepositories, _project: Project) -> None:
            """Write a printed number of the placeholder.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: The project as the change finds it, which the work does not need.
            :type _project: Project
            """
            repos.pages.update(evolve(repos.pages.get(placeholder.id), manual_label=RIVAL_LABEL))

        async def bind() -> None:
            """Bind the scan to the placeholder and keep the overview."""
            bound.append(await binding.attach_scan(fx_actor, book.project.id, placeholder.id, scan.id))

        # The hold ends first, when the block ends, and the group then waits for the changes that queued behind it
        async with anyio.create_task_group() as group, hold_book_in_thread(fx_uow(), book.project.id):
            group.start_soon(fx_uow().change_book, book.project.id, write_the_number)
            await anyio.wait_all_tasks_blocked()
            group.start_soon(bind)
            await anyio.wait_all_tasks_blocked()
            expect(not bound)

        [overview] = bound
        expect((overview.page.origin, overview.page.scan_id) == (PageOrigin.SCAN, scan.id))
        expect(overview.label == RIVAL_LABEL)
        assert_expectations()

    async def test_keeps_the_label_the_placeholder_had(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the scan's own label fills only an empty label.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        await fx_uow().change_book(
            book.project.id, lambda repos, _project: repos.pages.update(evolve(placeholder, manual_label=FIRST_LABEL))
        )
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)

        overview = await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        assert overview.label == FIRST_LABEL

    async def test_scan_of_another_project_and_an_unknown_scan_are_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a scan is bound only through the project that holds it, and the placeholder stays as it was.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        elsewhere = await _commit_book(fx_uow(), fx_asset_store, fx_actor)

        with pytest.raises(NotFoundError):
            await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, elsewhere.scans[0].id)
        with pytest.raises(NotFoundError):
            await fx_service().attach_scan(
                fx_actor, book.project.id, placeholder.id, make_scan(source=book.source, number=9).id
            )

        assert await fx_uow().read_book(book.project.id, lambda repos: repos.pages.get(placeholder.id)) == placeholder

    @pytest.mark.parametrize('origin', [PageOrigin.SCAN, PageOrigin.BLANK])
    async def test_page_that_is_not_a_placeholder_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        origin: PageOrigin,
    ) -> None:
        """Verify a page that has an image already, from a scan or generated, cannot be bound to another scan.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param origin: Origin of the page the scan is bound to.
        :type origin: PageOrigin
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)
        page = book.pages[0]
        if origin is PageOrigin.BLANK:
            page = evolve(make_page(order_key='a9'), origin=PageOrigin.BLANK)
            await fx_uow().change_book(book.project.id, lambda repos, _project: repos.pages.add(page))

        with pytest.raises(NotAPlaceholderError):
            await fx_service().attach_scan(fx_actor, book.project.id, page.id, scan.id)

    async def test_scan_another_page_shows_conflicts_naming_the_place_of_that_page(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a scan the import already made a page of is refused without ``take_over``, and nothing changes.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        before = await _stored_pages(fx_uow(), book.project)

        with pytest.raises(ScanAlreadyInBookError, match='position 1'):
            await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, book.scans[0].id)

        assert await _stored_pages(fx_uow(), book.project) == before

    async def test_take_over_deletes_the_page_of_the_scan_with_its_versions_and_files(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_storage_root: Path,
    ) -> None:
        """Verify the placeholder takes the scan from the page the import made of it, which is removed with its files.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        keys = ProjectKeys(book.project.id)
        async with fx_asset_store.writable(keys.version_rendition(book.versions[0], Rendition.FULL_JPEG)) as path:
            await anyio.Path(path).write_bytes(IMAGE)
        taken_directory = fx_storage_root / keys.page(book.pages[0].id)
        assert taken_directory.is_dir()

        overview = await fx_service().attach_scan(
            fx_actor, book.project.id, placeholder.id, book.scans[0].id, take_over=True
        )

        expect(book.pages[0].id not in {page.id for page in await _stored_pages(fx_uow(), book.project)})
        expect(overview.page.scan_id == book.scans[0].id)
        expect(
            not await fx_uow().read_book(
                book.project.id, lambda repos: repos.page_versions.list_by_ids([book.versions[0].id])
            )
        )
        expect(not taken_directory.exists())
        assert_expectations()

    async def test_take_over_is_one_change_of_the_pages(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify the page that went and the page that took its scan are one change of the book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        fx_book_changes.published.clear()

        await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, book.scans[0].id, take_over=True)

        assert [BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True]

    async def test_scan_without_renditions_conflicts(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a scan the import has not cut yet cannot be bound, since its image does not exist to copy.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)
        await fx_uow().change_book(
            book.project.id,
            lambda repos, _project: repos.scans.update(evolve(scan, renditions=Renditions(ready=False))),
        )

        with pytest.raises(ConflictError):
            await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)


class TestDelete:
    """Tests for PageService.delete()."""

    async def test_removes_the_page_its_versions_and_files_and_keeps_the_scan_and_the_source(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_storage_root: Path,
    ) -> None:
        """Verify the page and everything under ``assets/pages/<page_id>`` go, and a scan can be bound again.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, [FIRST_SCAN_SIZE, FIRST_SCAN_SIZE])
        keys = ProjectKeys(book.project.id)
        doomed, kept = book.pages
        for version in book.versions:
            async with fx_asset_store.writable(keys.version_rendition(version, Rendition.FULL_JPEG)) as path:
                await anyio.Path(path).write_bytes(IMAGE)

        await fx_service().delete(fx_actor, book.project.id, doomed.id)

        stored_ids = {page.id for page in await _stored_pages(fx_uow(), book.project)}
        versions = [
            *await _stored_versions(fx_uow(), book.project.id, doomed),
            *await _stored_versions(fx_uow(), book.project.id, kept),
        ]
        scans = await fx_uow().read_book(book.project.id, lambda repos: repos.scans.list_by_ids([book.scans[0].id]))
        sources = await fx_uow().read_book(book.project.id, lambda repos: repos.sources.list_all())
        expect(doomed.id not in stored_ids and kept.id in stored_ids)
        expect([version.page_id for version in versions] == [kept.id])
        expect(not (fx_storage_root / keys.page(doomed.id)).exists())
        expect((fx_storage_root / keys.page(kept.id)).is_dir())
        expect([scan.id for scan in scans] == [book.scans[0].id] and book.source.id in {each.id for each in sources})
        assert_expectations()

    async def test_a_deletion_is_one_change_of_the_pages(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a deleted page is one change of the book after the commit.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        fx_book_changes.published.clear()

        await fx_service().delete(fx_actor, book.project.id, book.pages[0].id)

        assert [BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True]

    async def test_page_of_another_project_and_of_another_account_are_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_stranger: Actor,
    ) -> None:
        """Verify a page is deleted only through the project that holds it, and only by the owner.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_stranger: Account of another user.
        :type fx_stranger: Actor
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        stranger = await _commit_book(fx_uow(), fx_asset_store, fx_stranger)

        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, book.project.id, stranger.pages[0].id)
        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, stranger.project.id, stranger.pages[0].id)

        assert len(await _stored_pages(fx_uow(), book.project, stranger.project)) == 2


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
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
        fx_storage_root: Path,
    ) -> None:
        """Verify the job leaves a ready ``pages.blank`` version whose full image is a white 1-bit PNG, and its renditions.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        size = PageSize(width_px=64, height_px=48, dpi=150.0)
        overview = await fx_service().add(
            fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.ENDPAPER, size=size)
        )

        await fx_board.run_queued()

        [version] = await _stored_versions(fx_uow(), project.id, overview.page)
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
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
        fx_storage_root: Path,
    ) -> None:
        """Verify the version of a page bound to a scan holds a byte copy of the scan's full image, and is ready.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the job board of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)
        service = make_page_service(fx_uow(), fx_asset_store, fx_runtime)
        await service.attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)

        await fx_runtime[2].run_queued()

        [version] = await _stored_versions(fx_uow(), book.project.id, placeholder)
        copy = fx_storage_root / ProjectKeys(book.project.id).version_rendition(version, Rendition.FULL_JPEG)
        expect(version.state is VersionState.READY)
        expect(version.renditions == Renditions(ready=True, full=Rendition.FULL_JPEG))
        expect(copy.read_bytes() == IMAGE)
        assert_expectations()

    async def test_announces_each_ready_version_and_ends_the_job_succeeded(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify the viewer is told of every version as it is ready, and the job's progress and state end complete.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        size = PageSize(width_px=8, height_px=8)
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size)
        first = await fx_service().add(fx_actor, project.id, new)
        second = await fx_service().add(fx_actor, project.id, new)

        await fx_board.run_queued()

        ready = [event.version.page_id for event in fx_events.published if isinstance(event, PageVersionReady)]
        [job] = _prepare_jobs(fx_board)
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
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
        fx_clock: FixedClock,
    ) -> None:
        """Verify a version the maker failed on is stored as failed, the job ends failed, and a later job makes it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        flaky = FlakyRenditionWriter(fail_first=1)

        def service() -> PageService:
            """Build a service whose maker of blank leaves fails once, over the board that runs its jobs.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(fx_uow(), fx_asset_store, (fx_events, fx_clock, board), renditions=flaky)

        board = _board_that_runs(service, fx_events, fx_clock)
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        new = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=PageSize(width_px=8, height_px=8))
        first = await service().add(fx_actor, project.id, new)

        await board.run_queued()

        [failed] = await _stored_versions(fx_uow(), project.id, first.page)
        [failed_job] = _prepare_jobs(board)
        expect(failed.state is VersionState.FAILED)
        expect(
            VersionData.ERROR in failed.data and failed.renditions == Renditions(ready=False, full=Rendition.FULL_PNG)
        )
        expect((failed_job.state, failed_job.error) == (JobState.FAILED, PAGES_FAILED.format(count=1)))
        second = await service().add(fx_actor, project.id, new)
        await board.run_queued()
        [again] = await _stored_versions(fx_uow(), project.id, first.page)
        [_, later_job] = _prepare_jobs(board)
        expect(again.state is VersionState.READY)
        expect(VersionData.ERROR not in again.data)
        expect((await _stored_versions(fx_uow(), project.id, second.page))[0].state is VersionState.READY)
        expect(later_job.state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_page_whose_scan_was_deleted_fails_with_that_reason(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
    ) -> None:
        """Verify a bound page whose source was deleted before the job ran has nothing to copy, and says so.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        book, placeholder = await _placeholder_book(fx_uow(), fx_asset_store, fx_actor)
        scan = await _spare_scan(fx_uow(), fx_asset_store, book)
        await fx_service().attach_scan(fx_actor, book.project.id, placeholder.id, scan.id)
        await fx_uow().change_book(book.project.id, lambda repos, _project: repos.sources.delete(scan.source_id))

        await fx_board.run_queued()

        [version] = await _stored_versions(fx_uow(), book.project.id, placeholder)
        expect(version.state is VersionState.FAILED)
        expect('deleted' in version.data[VersionData.ERROR])
        assert_expectations()

    async def test_job_cancelled_while_queued_does_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
        fx_events: RecordingEventBus,
    ) -> None:
        """Verify a job that is cancelled before the board takes it never runs and leaves its versions pending.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        size = PageSize(width_px=8, height_px=8)
        overview = await fx_service().add(
            fx_actor, project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size)
        )
        [job] = fx_board.put_jobs
        await fx_board.cancel(job.id)
        published = len(fx_events.published)

        await fx_board.run_queued()

        expect(fx_board.get(job.id).state is JobState.CANCELLED)
        expect((await _stored_versions(fx_uow(), project.id, overview.page))[0].state is VersionState.PENDING)
        expect(len(fx_events.published) == published)
        assert_expectations()

    async def test_job_with_nothing_to_prepare_succeeds(
        self, fx_uow: UnitOfWorkFactory, fx_actor: Actor, fx_board: FakeJobBoard
    ) -> None:
        """Verify a job that finds no pending or failed version ends succeeded with no steps.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        job = fx_board.put(make_job(project_id=project.id, kind=JobKind.PREPARE_PAGES))

        await fx_board.run_queued()

        stored = fx_board.get(job.id)
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
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
        fx_storage_root: Path,
    ) -> TwoLeaves:
        """Commit a project with two blank leaves, each queued for its image.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account owning the project.
        :type fx_actor: Actor
        :param fx_runtime: The recording bus, the stopped clock and the job board of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        :param fx_storage_root: Storage root of the test.
        :type fx_storage_root: Path
        :returns: The project, its two pages and the storage root.
        :rtype: TwoLeaves
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        pages = await _add_leaves(
            lambda: make_page_service(fx_uow(), fx_asset_store, fx_runtime),
            fx_actor,
            project,
            2,
        )
        return TwoLeaves(project=project, pages=pages, root=fx_storage_root)

    @pytest.mark.parametrize('victim', [WRITTEN_VICTIM, 'other'])
    async def test_a_page_deleted_while_the_job_runs_is_nothing_to_make_and_not_a_failure(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
        fx_leaves: TwoLeaves,
        victim: str,
    ) -> None:
        """Verify the job ends succeeded with the other page ready and no file left for the deleted one.

        The page is deleted and committed by another transaction while the first leaf is written. In one case it is
        the page being written, whose version is gone when the job stores it, and in the other the page that comes
        next, which is gone when the job reaches it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the job board of the test.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        :param fx_leaves: The project and its two leaves.
        :type fx_leaves: TwoLeaves
        :param victim: Whether the page deleted is the one being written or the other one.
        :type victim: str
        """
        events, clock, board = fx_runtime
        project, pages, root = fx_leaves
        deleted: list[PageId] = []

        async def delete_in_another_transaction(written: PageId) -> None:
            """Delete a page of the book and commit, as another request would.

            :param written: The page whose leaf the job is writing.
            :type written: PageId
            """
            page_id = written if victim == WRITTEN_VICTIM else next(page.id for page in pages if page.id != written)
            await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.delete(page_id))
            deleted.append(page_id)

        service = make_page_service(
            fx_uow(),
            fx_asset_store,
            fx_runtime,
            renditions=ActingRenditionWriter(delete_in_another_transaction),
        )

        # The board of the fixture holds the job of the two leaves, and it runs here through the service that deletes
        runner_board = _board_that_runs(lambda: service, events, clock)
        queued = runner_board.put(board.put_jobs[0])
        await runner_board.run_queued()

        [gone] = deleted
        [kept] = [page for page in pages if page.id != gone]
        job = runner_board.get(queued.id)
        expect((job.state, job.error, job.progress.done, job.progress.total) == (JobState.SUCCEEDED, '', 2, 2))
        expect(
            [version.state for version in await _stored_versions(fx_uow(), project.id, kept)] == [VersionState.READY]
        )
        expect(await _stored_versions(fx_uow(), project.id, next(page for page in pages if page.id == gone)) == [])
        expect(
            [event.version.page_id for event in events.published if isinstance(event, PageVersionReady)] == [kept.id]
        )
        expect(not (root / ProjectKeys(project.id).page(gone)).exists())
        assert_expectations()

    async def test_unexpected_error_in_the_files_of_a_version_fails_the_version_and_the_job_and_frees_the_project(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
        fx_clock: FixedClock,
    ) -> None:
        """Verify an error nobody expected stores the version as failed with its reason, ends the job failed, and frees the project.

        The job does not catch an error of its own, so the one of a version is caught where the version is made. A job
        left active would stop every later page from queueing an image job.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        flaky = FlakyRenditionWriter(fail_first=1)

        def service() -> PageService:
            """Build a service whose maker of blank leaves fails once, over the board that runs its jobs.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(fx_uow(), fx_asset_store, (fx_events, fx_clock, board), renditions=flaky)

        board = _board_that_runs(service, fx_events, fx_clock)
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        [leaf] = await _add_leaves(service, fx_actor, project, 1)

        await board.run_queued()

        [job] = _prepare_jobs(board)
        [version] = await _stored_versions(fx_uow(), project.id, leaf)
        expect((version.state, version.data[VersionData.ERROR]) == (VersionState.FAILED, UNEXPECTED_FAILURE))
        expect((job.state, job.error) == (JobState.FAILED, PAGES_FAILED.format(count=1)))
        expect(job.finished_at is not None)
        await _add_leaves(service, fx_actor, project, 1)
        expect(len(_prepare_jobs(board)) == FAILED_AND_NEXT_JOBS)
        assert_expectations()

    async def test_a_page_added_while_a_job_runs_gets_no_second_job_and_a_follow_up_when_it_ends(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_events: RecordingEventBus,
        fx_clock: FixedClock,
    ) -> None:
        """Verify one job runs at a time, and the version committed meanwhile is not missed.

        A leaf is added in another request while the first job writes its leaf. That request finds the job running
        and puts none, and the job, which had read its versions before, puts one more when it ends, which the board
        runs after it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_events: Recording event bus of the test.
        :type fx_events: RecordingEventBus
        :param fx_clock: Clock stopped at the epoch.
        :type fx_clock: FixedClock
        """
        queued_while_running: list[int] = []
        added: list[Page] = []

        def request_service() -> PageService:
            """Build the service of a new request, which puts its jobs on the board that runs them.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(fx_uow(), fx_asset_store, (fx_events, fx_clock, board))

        async def add_a_leaf(_: PageId) -> None:
            """Add a leaf in a request of its own, and note how many jobs are put afterwards.

            :param _: The page whose leaf the job is writing.
            :type _: PageId
            """
            added.extend(await _add_leaves(request_service, fx_actor, project, 1))
            queued_while_running.append(len(_prepare_jobs(board)))

        acting = ActingRenditionWriter(add_a_leaf)

        def job_service() -> PageService:
            """Build the service a job runs through, whose first leaf is written while another request adds one.

            :returns: The page service of a new unit of work.
            :rtype: PageService
            """
            return make_page_service(fx_uow(), fx_asset_store, (fx_events, fx_clock, board), renditions=acting)

        board = _board_that_runs(job_service, fx_events, fx_clock)
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        [first] = await _add_leaves(request_service, fx_actor, project, 1)

        await board.run_queued()

        expect(queued_while_running == [1])
        expect([job.kind for job in _prepare_jobs(board)] == [JobKind.PREPARE_PAGES, JobKind.PREPARE_PAGES])
        expect(
            [(await _stored_versions(fx_uow(), project.id, page))[0].state for page in (first, *added)]
            == [VersionState.READY, VersionState.READY]
        )
        expect({board.get(job.id).state for job in board.put_jobs} == {JobState.SUCCEEDED})
        assert_expectations()

    async def test_second_leaf_while_a_job_is_queued_queues_no_second_job(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
    ) -> None:
        """Verify a job queued or running is waited for by later pages, since a book has one such job at a time.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        await _add_leaves(fx_service, fx_actor, project, 3)

        assert _job_kinds(fx_board) == [(JobKind.PREPARE_PAGES, JobState.QUEUED)]


class TestAddBlankWithoutSizedPages:
    """Tests for the size of a blank leaf in a book whose pages give none."""

    async def test_book_whose_scan_pages_are_all_kept_out_says_it_is_the_recorded_size_that_is_missing(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the refusal names what the median needs, and does not claim that the pages have no image.

        The pages have images and sizes, but they are kept out of the book, so none is counted. A size given still
        makes the leaf.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, SIZES)
        await fx_uow().change_book(
            book.project.id,
            lambda repos, _project: repos.pages.update_many([evolve(page, included=False) for page in book.pages]),
        )
        blank = NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)

        with pytest.raises(ConflictError, match='cut from a scan and part of the book has a recorded size') as refused:
            await fx_service().add(fx_actor, book.project.id, blank)
        made = await fx_service().add(fx_actor, book.project.id, evolve(blank, size=PageSize(width_px=8, height_px=9)))

        expect('has an image' not in str(refused.value))
        made_version = (await _stored_versions(fx_uow(), book.project.id, made.page))[0]
        expect(PageSize.from_data(made_version.data) == PageSize(width_px=8, height_px=9))
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


async def _labels_in_book_order(uow: UnitOfWork, project: Project) -> list[str]:
    """Read the labels the committed pages of a project show, in the order of the book.

    :param uow: Unit of work to read through.
    :type uow: UnitOfWork
    :param project: Project whose pages are read.
    :type project: Project
    :returns: The label of each page, in order key order.
    :rtype: list[str]
    """
    return (await stored_book(uow, project.id)).labels


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

    async def test_adds_the_whole_list_at_the_end_in_order_in_one_change_and_one_key_call(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify pages without a place stand at the end in the order listed, in one change, with one key call.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the job board.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        keys = CountingOrderKeys()
        service = make_page_service(fx_uow(), fx_asset_store, fx_runtime, order_keys=keys)
        fx_book_changes.published.clear()

        added = await service.add_many(fx_actor, book.project.id, [_placeholder(str(n)) for n in range(1, 6)])

        expect([overview.position for overview in added] == [1, 2, 3, 4, 5])
        expect(await _labels_in_book_order(fx_uow(), book.project) == ['', '1', '2', '3', '4', '5'])
        expect(keys.calls == 1)
        expect([BookResource.PAGES in parts for parts in fx_book_changes.changed_parts()] == [True])
        assert_expectations()

    async def test_pages_without_a_place_follow_the_page_before_them_and_each_anchor_is_one_place(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard],
        fx_actor: Actor,
    ) -> None:
        """Verify a run starts at its anchor, continues after the page before it, and keeps the order listed.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_runtime: The recording bus, the stopped clock and the job board.
        :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, [FIRST_SCAN_SIZE] * 3)
        first, _, last = book.pages
        keys = CountingOrderKeys()
        service = make_page_service(fx_uow(), fx_asset_store, fx_runtime, order_keys=keys)
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

        expect(await _labels_in_book_order(fx_uow(), book.project) == ['', 'c', 'd', '', 'a', 'b', 'e', ''])
        expect([overview.label for overview in added] == ['a', 'b', 'c', 'd', 'e'])
        expect([overview.position for overview in added] == [4, 5, 1, 2, 6])
        expect(keys.calls == 2)
        assert_expectations()

    async def test_blank_leaves_get_one_pending_version_each_and_one_job(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_board: FakeJobBoard,
    ) -> None:
        """Verify blank leaves take the median size once, a given size is kept, and one job is queued for all.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_board: Board that records the jobs put on it.
        :type fx_board: FakeJobBoard
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor, SIZES)
        sized = PageSize(width_px=8, height_px=9)
        new = [
            NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK),
            _placeholder('p'),
            NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=sized),
        ]

        added = await fx_service().add_many(fx_actor, book.project.id, new)

        sizes = [
            PageSize.from_data((await _stored_versions(fx_uow(), book.project.id, added[i].page))[0].data)
            for i in (0, 2)
        ]
        expect(sizes == [PageSize(width_px=200, height_px=300, dpi=200.0), sized])
        expect(await _stored_versions(fx_uow(), book.project.id, added[1].page) == [])
        expect([job.kind for job in fx_board.put_jobs] == [JobKind.PREPARE_PAGES])
        assert_expectations()

    async def test_a_bad_anchor_in_the_middle_names_its_index_and_stores_nothing(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify an anchor that is no page of the book refuses the whole list, and no page or event is left.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_book_changes: Where the units of work of the test announce the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        lost = PageAnchor(page_id=PageId(UUID(int=7)), side=Side.AFTER)
        new = [_placeholder('1'), _placeholder('2'), _placeholder('3', lost)]

        fx_book_changes.published.clear()
        with pytest.raises(ConflictError, match='index 2 of the list'):
            await fx_service().add_many(fx_actor, book.project.id, new)

        expect(len(await _stored_pages(fx_uow(), book.project)) == len(book.pages))
        expect(fx_book_changes.published == [])
        assert_expectations()

    async def test_a_blank_leaf_without_a_size_in_a_book_without_images_names_its_index(
        self, fx_service: Callable[[], PageService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the first leaf that lacks a size is named, and the placeholders before it are not stored.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        new = [_placeholder('1'), NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)]

        with pytest.raises(ConflictError, match='index 1 of the list'):
            await fx_service().add_many(fx_actor, project.id, new)

        assert await _stored_pages(fx_uow(), project) == []

    async def test_refuses_more_pages_than_the_limit_and_a_project_of_another_account(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
        fx_stranger: Actor,
    ) -> None:
        """Verify a list over ``MAX_PAGES_PER_BATCH`` is a conflict and a stranger's project is not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store of the test.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_stranger: Account of another user.
        :type fx_stranger: Actor
        """
        book = await _commit_book(fx_uow(), fx_asset_store, fx_actor)
        stranger = await _commit_book(fx_uow(), fx_asset_store, fx_stranger)
        too_many = [_placeholder('x')] * (fx_service().MAX_PAGES_PER_BATCH + 1)

        with pytest.raises(ConflictError):
            await fx_service().add_many(fx_actor, book.project.id, too_many)
        with pytest.raises(NotFoundError):
            await fx_service().add_many(fx_actor, stranger.project.id, [_placeholder('1')])

        stored = await _stored_pages(fx_uow(), book.project, stranger.project)
        assert len(stored) == len(book.pages) + len(stranger.pages)

    async def test_a_thousand_pages_are_one_transaction(
        self,
        fx_service: Callable[[], PageService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify the largest list is added whole and in order.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        labels = [str(number) for number in range(fx_service().MAX_PAGES_PER_BATCH)]

        await fx_service().add_many(fx_actor, project.id, [_placeholder(label) for label in labels])

        assert await _labels_in_book_order(fx_uow(), project) == labels
