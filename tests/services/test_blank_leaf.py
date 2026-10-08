"""Tests for the choice of the image of a blank page: its scan, a white leaf, or a leaf of the paper of the book.

A leaf is a version of the page order that ``pages.blank`` makes, in place of the scan of a blank page, and the scan of
the page stays. The tests run the real processor on real images, through the page service, the way a request and the
``prepare-pages`` job of a worker would.
"""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import (
    BlankFill,
    BlankParam,
    JobState,
    NewPageOrigin,
    NormalizeParam,
    PageChange,
    PageKind,
    PageOrigin,
    PaperFill,
    RecipeKind,
    Rendition,
    Stage,
    StageState,
    VersionData,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPage, ProcessorRef, Renditions, Step
from bookreviver.services.base_versions import PAGES_BLANK, BaseVersions
from tests.helpers.builders import (
    EPOCH,
    make_page,
    make_page_stage,
    make_page_version,
    make_project,
    make_recipe,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.page_services import make_page_service
from tests.helpers.samples import pixel, ruled_page, same_colour
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.adapters.clock.system import FixedClock
    from bookreviver.adapters.jobs.recording import RecordingJobQueue
    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, PageStage, PageVersion, Project
    from bookreviver.services.pages import PageService
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

PAPER: tuple[int, int, int] = (205, 185, 140)
INK: tuple[int, int, int] = (25, 20, 20)
SCAN_WIDTH_PX: int = 120
SCAN_HEIGHT_PX: int = 160
SCAN_DPI: float = 100.0
NORMALIZED_SIZE: tuple[int, int] = (300, 420)
GEOMETRY_VERSION_ID: PageVersionId = PageVersionId('fedcba9876543210')
NORMALIZE_KEY: str = 'geometry.normalize'
RGB: str = 'RGB'
BILEVEL: str = '1'


def leaf_params(width_px: int, height_px: int) -> dict[str, object]:
    """Build the parameters of a white leaf of a size, which the resolution of the book is given to.

    :param width_px: Width of the leaf in pixels.
    :type width_px: int
    :param height_px: Height of the leaf in pixels.
    :type height_px: int
    :returns: The size and the resolution of the scans.
    :rtype: dict[str, object]
    """
    return {VersionData.WIDTH_PX: width_px, VersionData.HEIGHT_PX: height_px, VersionData.DPI: SCAN_DPI}


class LeafBook(NamedTuple):
    """A book of pages cut from scans whose images are stored.

    :ivar project: The project.
    :ivar pages: The pages in book order, with the kinds the book was committed with.
    :ivar versions: The ready base versions of the pages, in the same order.
    """

    project: Project
    pages: list[Page]
    versions: list[PageVersion]


class LeafDesk:
    """What the tests of the choice of leaves share: the database, the stores, the services and what they reported.

    :ivar database: In-memory database the requests and the jobs share.
    :ivar assets: Asset store of the test.
    :ivar actor: Account acting.
    :ivar queue: Queue recording the jobs the service hands to the workers.
    :ivar events: Bus recording what the service published.
    :ivar storage_root: Directory the asset store keeps its files under.
    """

    def __init__(
        self,
        database: InMemoryDatabase,
        assets: LocalAssetStore,
        actor: Actor,
        runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
        storage_root: Path,
    ) -> None:
        """Keep the parts a request is built from.

        :param database: In-memory database of the test.
        :type database: InMemoryDatabase
        :param assets: Local asset store of the test.
        :type assets: LocalAssetStore
        :param actor: Account the service acts for.
        :type actor: Actor
        :param runtime: The recording bus, the stopped clock and the recording queue.
        :type runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
        :param storage_root: Directory the asset store keeps its files under.
        :type storage_root: Path
        """
        self.database = database
        self.storage_root = storage_root
        self.assets = assets
        self.actor = actor
        self.events, _, self.queue = runtime
        self._runtime = runtime

    def service(self) -> PageService:
        """Build the page service for one request or job.

        :returns: A service over a new unit of work.
        :rtype: PageService
        """
        return make_page_service(InMemoryUnitOfWork(self.database), self.assets, self._runtime)

    async def book(self, kinds: Sequence[PageKind]) -> LeafBook:
        """Commit a book with a page of each kind, cut from a scan of text on yellowed paper, and store the images.

        :param kinds: Kind of each page, in book order.
        :type kinds: Sequence[PageKind]
        :returns: The stored book.
        :rtype: LeafBook
        """
        project = make_project(owner_id=self.actor.account_id)
        source = make_source(project_id=project.id)
        scans, pages, versions = [], [], []
        for number, kind in enumerate(kinds):
            scan = make_scan(source=source, number=number)
            facts = evolve(scan.facts, width_px=SCAN_WIDTH_PX, height_px=SCAN_HEIGHT_PX, dpi_x=SCAN_DPI, dpi_y=SCAN_DPI)
            scan = evolve(scan, facts=facts, renditions=Renditions(ready=True, full=Rendition.FULL_PNG))
            page = make_page(project_id=project.id, order_key=f'a{number}', scan=scan, kind=kind)
            scans.append(scan)
            pages.append(page)
            versions.append(BaseVersions.split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH))
        await commit_project(self.database, project, *pages, sources=[source], scans=scans, versions=versions)
        # The import makes the base version of each page the current version of the page split
        uow = InMemoryUnitOfWork(self.database)
        for page, version in zip(pages, versions, strict=True):
            await uow.page_stages.save(
                make_page_stage(page_id=page.id, stage=Stage.PAGE_SPLIT, head_version_id=version.id)
            )
        await uow.commit()
        keys = ProjectKeys(project.id)
        for version in versions:
            async with self.assets.writable(keys.version_rendition(version, Rendition.FULL_PNG)) as target:
                ruled_page((SCAN_WIDTH_PX, SCAN_HEIGHT_PX), PAPER, INK).save(target, format='PNG')
        return LeafBook(project=project, pages=pages, versions=versions)

    async def choose(self, book: LeafBook, positions: Sequence[int], fill: BlankFill) -> None:
        """Choose the image of the pages at the positions, as the route does.

        :param book: The book.
        :type book: LeafBook
        :param positions: Positions of the pages, from zero.
        :type positions: Sequence[int]
        :param fill: What their image becomes.
        :type fill: BlankFill
        """
        ids = [book.pages[position].id for position in positions]
        await self.service().set_blank_fill(self.actor, book.project.id, ids, fill)

    async def prepare(self) -> None:
        """Run the ``prepare-pages`` job that was queued last, as a worker would."""
        await self.service().prepare_images(self.queue.enqueued[-1].id)

    def announced(self) -> list[PagesChanged]:
        """Read the announcements of changed pages the service published, without the events of its jobs.

        :returns: The ``PagesChanged`` events, in the order they were published.
        :rtype: list[PagesChanged]
        """
        return [event for event in self.events.published if isinstance(event, PagesChanged)]

    def page(self, page: Page) -> Page:
        """Read the committed state of a page.

        :param page: The page.
        :type page: Page
        :returns: The page as committed.
        :rtype: Page
        """
        return self.database.tables.pages[page.id]

    def leaves(self, page: Page) -> list[PageVersion]:
        """Read the committed versions of a page that ``pages.blank`` made, the earliest first.

        :param page: The page.
        :type page: Page
        :returns: Its leaves.
        :rtype: list[PageVersion]
        """
        found = [
            version
            for version in self.database.tables.page_versions.values()
            if version.page_id == page.id and version.processor == PAGES_BLANK
        ]
        return sorted(found, key=lambda version: (version.created_at, version.id))

    def heads(self, page: Page) -> dict[Stage, PageStage]:
        """Read the committed records of the stages of a page.

        :param page: The page.
        :type page: Page
        :returns: Its records by stage.
        :rtype: dict[Stage, PageStage]
        """
        return {key.stage: record for key, record in self.database.tables.page_stages.items() if key.page_id == page.id}

    def image(self, book: LeafBook, version: PageVersion) -> Image.Image:
        """Open the stored ``full`` image of a version.

        :param book: The book owning the page of the version.
        :type book: LeafBook
        :param version: A ready version with an image.
        :type version: PageVersion
        :returns: The image, loaded.
        :rtype: Image.Image
        """
        assert version.renditions is not None
        key = ProjectKeys(book.project.id).version_rendition(version, version.renditions.full)
        with Image.open(self.storage_root / key) as image:
            return image.copy()


@pytest.fixture
def fx_desk(
    fx_database: InMemoryDatabase,
    fx_asset_store: LocalAssetStore,
    fx_actor: Actor,
    fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
    fx_storage_root: Path,
) -> LeafDesk:
    """Build the desk of a test over its database, its asset store and the account acting.

    :param fx_database: In-memory database of the test.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Local asset store of the test.
    :type fx_asset_store: LocalAssetStore
    :param fx_actor: Account the service acts for.
    :type fx_actor: Actor
    :param fx_runtime: The recording bus, the stopped clock and the recording queue.
    :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
    :param fx_storage_root: Storage root of the test.
    :type fx_storage_root: Path
    :returns: The desk.
    :rtype: LeafDesk
    """
    return LeafDesk(fx_database, fx_asset_store, fx_actor, fx_runtime, fx_storage_root)


class TestChooseALeaf:
    """Tests for PageService.set_blank_fill() choosing a white leaf or the paper of the book."""

    async def test_a_white_leaf_is_a_pending_version_of_the_page_order_of_the_size_of_the_book(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify the page keeps its scan and gets a pending leaf of the median size, one job and one event.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK, PageKind.TEXT])
        blank = book.pages[1]

        await fx_desk.choose(book, [1], BlankFill.WHITE)

        [leaf] = fx_desk.leaves(blank)
        stored = fx_desk.page(blank)
        expect((stored.blank_fill, stored.origin, stored.scan_id) == (BlankFill.WHITE, PageOrigin.SCAN, blank.scan_id))
        expect((leaf.stage, leaf.state, leaf.input_id) == (Stage.PAGE_ORDER, VersionState.PENDING, None))
        expect(leaf.params == leaf_params(SCAN_WIDTH_PX, SCAN_HEIGHT_PX))
        expect(len(fx_desk.queue.enqueued) == 1)
        expect(
            fx_desk.announced()
            == [PagesChanged(project_id=book.project.id, page_ids=[blank.id], change=PageChange.EDITED)]
        )
        # The base version of the scan stays as it was
        expect(book.versions[1] in fx_desk.database.tables.page_versions.values())
        assert_expectations()

    async def test_the_job_draws_the_leaf_and_the_page_shows_it(self, fx_desk: LeafDesk) -> None:
        """Verify the version is ready with a white 1-bit image, current in the page order, and shown by the page.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)

        await fx_desk.prepare()

        [leaf] = fx_desk.leaves(blank)
        shown = await fx_desk.service().get(fx_desk.actor, book.project.id, blank.id)
        image = fx_desk.image(book, leaf)
        expect(leaf.state is VersionState.READY)
        expect(fx_desk.heads(blank)[Stage.PAGE_ORDER].head_version_id == leaf.id)
        expect(shown.image_version is not None and shown.image_version.id == leaf.id)
        expect((image.mode, image.size) == (BILEVEL, (SCAN_WIDTH_PX, SCAN_HEIGHT_PX)))
        assert_expectations()

    async def test_the_paper_of_the_book_is_taken_from_the_nearest_pages_of_text_only(self, fx_desk: LeafDesk) -> None:
        """Verify the leaf is made from the pages of text beside it, not from a cover or from another blank page.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        kinds = [PageKind.COVER, PageKind.TEXT, PageKind.BLANK, PageKind.TEXT, PageKind.BLANK, PageKind.TITLE]
        book = await fx_desk.book(kinds)
        blank = book.pages[2]

        await fx_desk.choose(book, [2], BlankFill.PAPER)
        await fx_desk.prepare()

        [leaf] = fx_desk.leaves(blank)
        wanted = sorted(book.versions[position].id for position in (1, 3, 5))
        image = fx_desk.image(book, leaf)
        expect(leaf.params[BlankParam.FILL] == PaperFill.PAPER)
        expect(leaf.params[BlankParam.PAPER_FROM] == wanted)
        expect((image.mode, image.size) == (RGB, (SCAN_WIDTH_PX, SCAN_HEIGHT_PX)))
        expect(same_colour(pixel(image, (0, 0)), PAPER) and same_colour(pixel(image, (SCAN_WIDTH_PX - 1, 5)), PAPER))
        assert_expectations()

    async def test_several_pages_change_in_one_transaction_with_one_event_and_one_job(self, fx_desk: LeafDesk) -> None:
        """Verify every page gets its leaf, and the browser is told once.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.BLANK, PageKind.TEXT, PageKind.BLANK])

        await fx_desk.choose(book, [0, 2], BlankFill.WHITE)

        [event] = fx_desk.announced()
        expect(set(event.page_ids) == {book.pages[0].id, book.pages[2].id})
        expect([len(fx_desk.leaves(book.pages[position])) for position in range(3)] == [1, 0, 1])
        expect(len(fx_desk.queue.enqueued) == 1)
        assert_expectations()

    async def test_a_page_that_has_the_choice_already_is_left_alone(self, fx_desk: LeafDesk) -> None:
        """Verify a repeated choice writes nothing, announces nothing and queues no second job.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        written = fx_desk.page(book.pages[1])

        await fx_desk.choose(book, [1], BlankFill.WHITE)

        expect(fx_desk.page(book.pages[1]) is written)
        expect(len(fx_desk.announced()) == 1)
        expect(len(fx_desk.queue.enqueued) == 1)
        assert_expectations()

    async def test_a_leaf_has_the_size_the_normalize_step_gives_the_pages_once_the_book_is_measured(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify the size of the normalize step of the recipe for blank pages wins over the median, and a step that is off does not.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK, PageKind.BLANK])
        recipe = make_recipe(project_id=book.project.id, kind=RecipeKind.BLANK)
        width, height = NORMALIZED_SIZE
        step = Step(
            processor_key=NORMALIZE_KEY, params={NormalizeParam.PAGE_WIDTH: width, NormalizeParam.PAGE_HEIGHT: height}
        )
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.recipes.add(evolve(recipe, steps=(step,)))
        await uow.commit()

        await fx_desk.choose(book, [1], BlankFill.WHITE)
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.recipes.update(evolve(recipe, steps=(evolve(step, enabled=False),)))
        await uow.commit()
        await fx_desk.choose(book, [2], BlankFill.WHITE)

        [normalized] = fx_desk.leaves(book.pages[1])
        [median] = fx_desk.leaves(book.pages[2])
        expect(normalized.params == leaf_params(width, height))
        expect(median.params == leaf_params(SCAN_WIDTH_PX, SCAN_HEIGHT_PX))
        assert_expectations()

    async def test_a_leaf_does_not_change_the_size_a_new_blank_page_takes_from_the_book(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify the leaves drawn in place of scans are no pages of the book to take a median size from.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        recipe = make_recipe(project_id=book.project.id, kind=RecipeKind.BLANK)
        step = Step(
            processor_key=NORMALIZE_KEY, params={NormalizeParam.PAGE_WIDTH: 500, NormalizeParam.PAGE_HEIGHT: 600}
        )
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.recipes.add(evolve(recipe, steps=(step,)))
        await uow.commit()
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.recipes.update(evolve(recipe, steps=(evolve(step, enabled=False),)))
        await uow.commit()

        added = await fx_desk.service().add(
            fx_desk.actor, book.project.id, NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK)
        )

        [made] = fx_desk.leaves(added.page)
        assert made.params == leaf_params(SCAN_WIDTH_PX, SCAN_HEIGHT_PX)


class TestGetTheScanBack:
    """Tests for PageService.set_blank_fill() choosing the scan, and for a change of kind."""

    async def test_choosing_the_scan_removes_the_leaf_from_the_page_order_and_keeps_the_scan(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify the page order has no current version again, and the page shows the scan it keeps.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()

        await fx_desk.choose(book, [1], BlankFill.SCAN)

        shown = await fx_desk.service().get(fx_desk.actor, book.project.id, blank.id)
        stored = fx_desk.page(blank)
        expect((stored.blank_fill, stored.scan_id, stored.origin) == (BlankFill.SCAN, blank.scan_id, PageOrigin.SCAN))
        expect(Stage.PAGE_ORDER not in fx_desk.heads(blank))
        expect(shown.image_version is not None and shown.image_version.id == book.versions[1].id)
        assert_expectations()

    async def test_the_stages_after_the_page_order_become_stale_when_the_image_changes(self, fx_desk: LeafDesk) -> None:
        """Verify the result of a later stage, made from the old image, is marked out of date both ways.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        geometry = evolve(
            make_page_version(page_id=blank.id),
            id=GEOMETRY_VERSION_ID,
            stage=Stage.GEOMETRY,
            state=VersionState.READY,
        )
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.page_versions.add(geometry)
        await uow.page_stages.save(make_page_stage(page_id=blank.id, head_version_id=geometry.id))
        await uow.commit()
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()
        after_leaf = fx_desk.heads(blank)[Stage.GEOMETRY].state
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.page_stages.save(make_page_stage(page_id=blank.id, head_version_id=geometry.id))
        await uow.commit()

        await fx_desk.choose(book, [1], BlankFill.SCAN)

        expect(after_leaf is StageState.STALE)
        expect(fx_desk.heads(blank)[Stage.GEOMETRY].state is StageState.STALE)
        assert_expectations()

    async def test_the_leaf_is_found_again_when_it_is_chosen_again(self, fx_desk: LeafDesk) -> None:
        """Verify choosing the leaf after the scan makes the leaf made before current at once, with no job.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()
        [leaf] = fx_desk.leaves(blank)
        await fx_desk.choose(book, [1], BlankFill.SCAN)

        await fx_desk.choose(book, [1], BlankFill.WHITE)

        expect(fx_desk.leaves(blank) == [leaf])
        expect(fx_desk.heads(blank)[Stage.PAGE_ORDER].head_version_id == leaf.id)
        expect(len(fx_desk.queue.enqueued) == 1)
        assert_expectations()

    async def test_a_leaf_still_pending_when_the_scan_is_chosen_is_dropped_by_the_job(self, fx_desk: LeafDesk) -> None:
        """Verify the job makes no leaf the page no longer shows, removes it, and ends succeeded with nothing pending.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.choose(book, [1], BlankFill.SCAN)

        await fx_desk.prepare()

        [job] = fx_desk.database.tables.jobs.values()
        expect(fx_desk.leaves(blank) == [])
        expect(Stage.PAGE_ORDER not in fx_desk.heads(blank))
        expect(job.state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_a_white_leaf_still_pending_when_the_paper_is_chosen_is_dropped_for_the_paper(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify only the leaf of the choice the page has now is made.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.choose(book, [1], BlankFill.PAPER)

        await fx_desk.prepare()

        [leaf] = fx_desk.leaves(blank)
        expect(leaf.params[BlankParam.FILL] == PaperFill.PAPER)
        expect(fx_desk.heads(blank)[Stage.PAGE_ORDER].head_version_id == leaf.id)
        assert_expectations()

    async def test_a_page_that_stops_being_blank_gets_its_scan_back(self, fx_desk: LeafDesk) -> None:
        """Verify changing the kind drops the leaf, as the choice of the scan does, and reports the page as changed.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()

        changed = await fx_desk.service().update(
            fx_desk.actor, book.project.id, blank.id, PageChanges(kind=PageKind.TEXT)
        )

        expect((changed.page.kind, changed.page.blank_fill) == (PageKind.TEXT, BlankFill.SCAN))
        expect((fx_desk.page(blank).kind, fx_desk.page(blank).blank_fill) == (PageKind.TEXT, BlankFill.SCAN))
        expect(Stage.PAGE_ORDER not in fx_desk.heads(blank))
        expect(changed.image_version is not None and changed.image_version.id == book.versions[1].id)
        assert_expectations()

    async def test_a_page_that_stays_blank_keeps_its_leaf_when_its_notes_change(self, fx_desk: LeafDesk) -> None:
        """Verify a change that leaves the kind alone leaves the leaf in place.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()

        await fx_desk.service().update(fx_desk.actor, book.project.id, blank.id, PageChanges(notes='Verso is clean'))

        expect(fx_desk.page(blank).blank_fill is BlankFill.WHITE)
        expect(Stage.PAGE_ORDER in fx_desk.heads(blank))
        assert_expectations()


class TestRemakeOutdatedLeaves:
    """Tests for PageService.remake_outdated_leaves() making the leaves of a replaced version of the processor again."""

    async def outdate(self, desk: LeafDesk, book: LeafBook, position: int) -> None:
        """Turn the leaf the page shows into one that the version 1 of the processor made, and mark its stage stale.

        The version 1 drew the paper of a book as a white leaf, so the old leaf is a white image under the old identifier.

        :param desk: What the tests of the choice of leaves share.
        :type desk: LeafDesk
        :param book: The book.
        :type book: LeafBook
        :param position: Position of the page, from zero.
        :type position: int
        """
        page = book.pages[position]
        [leaf] = desk.leaves(page)
        old = evolve(
            leaf, id=PageVersionId(f'{position:016x}'), processor=ProcessorRef(key=PAGES_BLANK.key, version='1')
        )
        tables = desk.database.tables
        del tables.page_versions[leaf.id]
        tables.page_versions[old.id] = old
        stale = evolve(desk.heads(page)[Stage.PAGE_ORDER], head_version_id=old.id, state=StageState.STALE)
        tables.page_stages[stale.key] = stale
        keys = ProjectKeys(book.project.id)
        assert old.renditions is not None
        async with desk.assets.writable(keys.version_rendition(old, old.renditions.full)) as target:
            Image.new(BILEVEL, (SCAN_WIDTH_PX, SCAN_HEIGHT_PX), 1).save(target, format='PNG')

    async def test_a_leaf_of_an_older_version_is_drawn_again_with_the_paper_and_its_stage_is_fresh(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify a stale record an earlier start left gets the leaf of the current version, by the same job.

        The service is given nothing but the database, so the page is found from its record alone.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK, PageKind.TEXT])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.PAPER)
        await fx_desk.prepare()
        await self.outdate(fx_desk, book, 1)
        queued = len(fx_desk.queue.enqueued)

        await fx_desk.service().remake_outdated_leaves()
        await fx_desk.prepare()

        [leaf] = fx_desk.leaves(blank)
        record = fx_desk.heads(blank)[Stage.PAGE_ORDER]
        image = fx_desk.image(book, leaf)
        expect(len(fx_desk.queue.enqueued) == queued + 1)
        expect((record.head_version_id, record.state) == (leaf.id, StageState.FRESH))
        expect((leaf.state, leaf.params[BlankParam.FILL]) == (VersionState.READY, PaperFill.PAPER))
        expect(same_colour(pixel(image, (0, 0)), PAPER))
        expect(fx_desk.page(blank).blank_fill is BlankFill.PAPER)
        assert_expectations()

    async def test_a_start_that_ended_before_the_job_ran_is_finished_by_the_next_one(self, fx_desk: LeafDesk) -> None:
        """Verify the pending leaf stays the only one and the job is queued again when the leaf was not written yet.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK, PageKind.TEXT])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.PAPER)
        await fx_desk.prepare()
        await self.outdate(fx_desk, book, 1)
        await fx_desk.service().remake_outdated_leaves()
        [pending] = fx_desk.leaves(blank)
        [first_job] = fx_desk.queue.enqueued[-1:]
        fx_desk.database.tables.jobs[first_job.id] = evolve(first_job, state=JobState.FAILED)

        await fx_desk.service().remake_outdated_leaves()
        await fx_desk.prepare()

        [leaf] = fx_desk.leaves(blank)
        expect((leaf.id, leaf.state) == (pending.id, VersionState.READY))
        expect(fx_desk.heads(blank)[Stage.PAGE_ORDER].state is StageState.FRESH)
        assert_expectations()

    async def test_a_leaf_of_the_current_version_and_a_stage_that_is_not_a_leaf_are_left_alone(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify no version is added and no job is queued when every leaf is of the installed version.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.TEXT, PageKind.BLANK])
        blank = book.pages[1]
        await fx_desk.choose(book, [1], BlankFill.WHITE)
        await fx_desk.prepare()
        queued, leaves = len(fx_desk.queue.enqueued), fx_desk.leaves(blank)

        await fx_desk.service().remake_outdated_leaves()

        expect(len(fx_desk.queue.enqueued) == queued)
        expect(fx_desk.leaves(blank) == leaves)
        assert_expectations()


class TestRefusals:
    """Tests for what PageService.set_blank_fill() refuses, which leaves every page as it was."""

    async def test_a_leaf_is_refused_for_a_page_that_is_not_blank_and_none_of_the_pages_change(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify one page of text among the blank ones stops the whole change.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.BLANK, PageKind.TEXT])

        with pytest.raises(ConflictError, match='not a blank page'):
            await fx_desk.choose(book, [0, 1], BlankFill.WHITE)

        expect([fx_desk.page(page).blank_fill for page in book.pages] == [BlankFill.SCAN, BlankFill.SCAN])
        expect(fx_desk.leaves(book.pages[0]) == [])
        expect(fx_desk.events.published == [])
        expect(fx_desk.queue.enqueued == [])
        assert_expectations()

    @pytest.mark.parametrize('fill', [BlankFill.WHITE, BlankFill.SCAN])
    async def test_a_page_not_cut_from_a_scan_has_no_scan_to_replace_or_to_keep(
        self, fx_desk: LeafDesk, fill: BlankFill
    ) -> None:
        """Verify a generated leaf and a placeholder are refused whichever choice is made.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        :param fill: The choice that is refused.
        :type fill: BlankFill
        """
        book = await fx_desk.book([PageKind.TEXT])
        generated = evolve(
            make_page(project_id=book.project.id, order_key='b0', kind=PageKind.BLANK), origin=PageOrigin.BLANK
        )
        waiting = make_page(project_id=book.project.id, order_key='b1', kind=PageKind.BLANK)
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.pages.add_many([generated, waiting])
        await uow.commit()

        for page in (generated, waiting):
            with pytest.raises(ConflictError, match='not cut from a scan'):
                await fx_desk.service().set_blank_fill(fx_desk.actor, book.project.id, [page.id], fill)

    async def test_a_page_the_project_lacks_is_not_found_and_so_is_a_project_of_another_account(
        self, fx_desk: LeafDesk
    ) -> None:
        """Verify an unknown page, a page of another project, and a project of another account answer not found.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.BLANK])
        other = make_project(owner_id=fx_desk.actor.account_id)
        stranger = make_page(project_id=other.id, kind=PageKind.BLANK)
        await commit_project(fx_desk.database, other, stranger)
        foreign = evolve(fx_desk.actor, account_id=new_account_id())

        with pytest.raises(NotFoundError):
            await fx_desk.service().set_blank_fill(fx_desk.actor, book.project.id, [stranger.id], BlankFill.WHITE)
        with pytest.raises(NotFoundError):
            await fx_desk.service().set_blank_fill(foreign, book.project.id, [book.pages[0].id], BlankFill.WHITE)

        assert fx_desk.leaves(book.pages[0]) == []

    async def test_a_book_without_a_recorded_size_has_no_size_to_give_the_leaf(self, fx_desk: LeafDesk) -> None:
        """Verify the answer is a conflict and nothing is stored when no page of the book records a size.

        :param fx_desk: What the tests of the choice of leaves share.
        :type fx_desk: LeafDesk
        """
        book = await fx_desk.book([PageKind.BLANK])
        uow = InMemoryUnitOfWork(fx_desk.database)
        await uow.page_versions.update(evolve(book.versions[0], data={}))
        await uow.commit()

        with pytest.raises(ConflictError, match='no size'):
            await fx_desk.choose(book, [0], BlankFill.WHITE)

        expect(fx_desk.page(book.pages[0]).blank_fill is BlankFill.SCAN)
        expect(fx_desk.events.published == [])
        assert_expectations()
