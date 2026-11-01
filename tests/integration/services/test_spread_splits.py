"""Tests for splitting a scan into the two pages of a spread, and undoing the split, with the real OpenCV plugin.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

import io
from functools import partial
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.geometry import Line, Point
from bookreviver.domain.ids import PageId
from bookreviver.domain.jobs import JobState, StageRun, StepPreview
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.page_steps import NewPageEdit
from bookreviver.domain.pages import Page
from bookreviver.domain.recipes import Step
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from bookreviver.domain.step_values import ValueScope
from bookreviver.domain.versions import VersionState
from tests.harness.builders.domain import make_page_stage, make_step_values, open_for
from tests.harness.builders.processing import stored_job
from tests.harness.builders.spreads import (
    HEIGHT_PX,
    PAGE_WIDTH_PX,
    SPLIT_SPREAD,
    book_of,
    head_of,
    run_stage,
    seed_spreads,
    stage_of,
    use_recipe,
)
from tests.harness.concurrency.meanwhile import change_while_writing
from tests.harness.images.samples import png_bytes, spread

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.slices import Slice
    from bookreviver.domain.stages import PageStage
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.adapters.database import LocatedBookDatabases
    from tests.harness.builders.domain import BookOpener
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# Where the cut line the user draws crosses the top and the bottom of the scan
CUT_LINE: Line = Line(start=Point(x=880, y=0), end=Point(x=900, y=HEIGHT_PX - 1))
# Where another run that split the scan first put its right half, after the one page of the book
RIGHT_HALF_ORDER_KEY: str = 'a1'
EVERYTHING: SliceRequest = SliceRequest(limit=100)


async def width_of(kit: ProcessingKit, book_id: BookId, version: PageVersion) -> int:
    """Read the width of the stored ``full`` image of a version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book owning the version's page.
    :type book_id: BookId
    :param version: A ready version that has its files.
    :type version: PageVersion
    :returns: The width in pixels of the image the version stored.
    :rtype: int
    """
    assert version.renditions is not None
    async with kit.files.assets(book_id).readable(BookKeys.version_rendition(version, version.renditions.full)) as full:
        with Image.open(io.BytesIO(full.read_bytes())) as image:
            return image.width


async def versions_of(book_id: BookId, page: Page, *, databases: LocatedBookDatabases) -> list[PageVersion]:
    """Read the versions of a page, the earliest first, as committed.

    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: Every version of the page.
    :rtype: list[PageVersion]
    """
    return list(await databases.open(book_id).read(lambda repos: repos.page_versions.list_for_page(page.id)))


async def give_odd_pages_a_value(book_id: BookId, page: Page, *, databases: LocatedBookDatabases) -> None:
    """Give the odd pages a value for a step of the geometry stage, and the page a fresh record of that stage.

    :param book_id: Identifier of the book owning the page.
    :type book_id: BookId
    :param page: The page that has been through the geometry stage.
    :type page: Page
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    """

    def save_value_and_record(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Save the value of the odd pages and the fresh record of the geometry stage.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the work does not need.
        :type _book_record: BookRecord
        """
        repos.step_values.save(make_step_values(scope=ValueScope.ODD))
        repos.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.GEOMETRY))

    await databases.open(book_id).change(save_value_and_record)


class TestSplit:
    """Tests for the run of a recipe whose step splits a scan."""

    async def test_a_page_after_the_new_half_goes_stale_where_the_odd_pages_have_a_value(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the new half moves the next spread from the second place to the third, an even page to an odd one.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first, second] = await seed_spreads(fx_cv_kit, count=2)
        await give_odd_pages_a_value(book_id, second, databases=fx_cv_kit.databases)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,)))
        assert (
            await stage_of(book_id, second, Stage.GEOMETRY, databases=fx_cv_kit.databases)
        ).state is StageState.STALE

    async def test_the_page_stays_the_left_half_and_the_right_half_follows_it(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a new page is inserted right after the page that was split, and the pages after it keep their order.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first, second] = await seed_spreads(fx_cv_kit, count=2)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,)))
        pages = await book_of(book_id, databases=fx_cv_kit.databases)
        expect([page.id for page in pages][::2] == [first.id, second.id])
        expect([page.slot for page in pages] == [Page.LEFT_HALF, Page.RIGHT_HALF, Page.WHOLE_SCAN])
        expect(pages[1].scan_id == first.scan_id)
        expect(first.order_key < pages[1].order_key < second.order_key)
        assert_expectations()

    async def test_each_half_gets_a_base_version_holding_its_part_of_the_scan(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the halves are the current versions of the stage, with pyramids and files that add up to the scan.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first] = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(book_id, databases=fx_cv_kit.databases)
        left_version = await head_of(fx_cv_kit, book_id, left, Stage.PAGE_SPLIT)
        right_version = await head_of(fx_cv_kit, book_id, right, Stage.PAGE_SPLIT)
        expect(left.id == first.id)
        for version in (left_version, right_version):
            expect((version.state, version.processor.key, version.input_id) == (VersionState.READY, SPLIT_SPREAD, None))
            expect(version.tiles_ready)
        expect(
            await width_of(fx_cv_kit, book_id, left_version) + await width_of(fx_cv_kit, book_id, right_version)
            == 2 * PAGE_WIDTH_PX
        )
        expect(
            (await stage_of(book_id, right, Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state is StageState.FRESH
        )
        expect(
            (await stage_of(book_id, left, Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state is StageState.FRESH
        )
        assert_expectations()

    async def test_both_versions_are_announced_ready(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the browser is told of the two versions that became ready, one for each half.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, _ = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(book_id, databases=fx_cv_kit.databases)
        ready = [event.version.page_id for event in fx_cv_kit.events.published if isinstance(event, PageVersionReady)]
        assert sorted(ready) == sorted([left.id, right.id])

    async def test_a_second_run_with_the_same_line_finds_the_versions_and_adds_no_page(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a repeated run runs no step, makes no version and no page.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first] = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        written = len(fx_cv_kit.writer.calls)
        # The run names the page, which a run of the whole book would leave out as fresh
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,)))
        expect(len(fx_cv_kit.writer.calls) == written)
        expect(len(await book_of(book_id, databases=fx_cv_kit.databases)) == 2)
        assert_expectations()

    async def test_a_run_over_the_whole_book_leaves_the_right_halves_to_their_left_halves(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the right half made by a run is not split again by the next run over every page.

        The second run names every page of the book, the right halves too, which a run of the whole book would leave
        out as fresh.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, _ = await seed_spreads(fx_cv_kit, count=2)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        every = StageRun(
            stage=Stage.PAGE_SPLIT,
            page_ids=tuple(page.id for page in await book_of(book_id, databases=fx_cv_kit.databases)),
        )
        await run_stage(fx_cv_kit, actor, book_id, every)
        expect([page.slot for page in await book_of(book_id, databases=fx_cv_kit.databases)] == [1, 2, 1, 2])
        assert_expectations()

    async def test_a_new_cut_line_makes_new_base_versions_of_both_halves_beside_the_old_ones(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify an edit of the line is read by the next run, and the versions of the old line are not deleted.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, _ = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(book_id, databases=fx_open_book.databases)
        before = [await head_of(fx_cv_kit, book_id, page, Stage.PAGE_SPLIT) for page in (left, right)]
        key = await fx_cv_kit.edit_key(book_id, left, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await fx_cv_kit.edits().save(
            fx_open_book(actor, book_id), key, NewPageEdit(kind=CUT_LINE.editor, geometry=CUT_LINE), None
        )
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        after = [await head_of(fx_cv_kit, book_id, page, Stage.PAGE_SPLIT) for page in (left, right)]
        every = [await versions_of(book_id, page, databases=fx_open_book.databases) for page in (left, right)]
        expect(all(new.id != old.id for new, old in zip(after, before, strict=True)))
        expect([len(versions) for versions in every] == [2, 2])
        expect(len(await book_of(book_id, databases=fx_open_book.databases)) == 2)
        assert_expectations()

    async def test_a_split_that_fails_leaves_the_book_as_it_was(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify no page is added and the page keeps its slot when the step fails, which the page's version says why.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id = await fx_cv_kit.seed_book()
        page, _ = await fx_cv_kit.seed_scan_page(book_id, image=b'this is no image')
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        [stored] = await book_of(book_id, databases=fx_cv_kit.databases)
        versions = await versions_of(book_id, page, databases=fx_cv_kit.databases)
        expect((stored.id, stored.slot) == (page.id, Page.WHOLE_SCAN))
        expect(
            [(version.state, version.processor.key) for version in versions] == [(VersionState.FAILED, SPLIT_SPREAD)]
        )
        expect('cannot be read' in versions[0].data['error'])
        expect(
            (await stage_of(book_id, page, Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state is StageState.FAILED
        )
        assert_expectations()

    async def test_a_split_that_failed_is_made_again_by_the_next_run(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the failed version is replaced when the scan can be read, and then the page has its right half.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id = await fx_cv_kit.seed_book()
        broken, _ = await fx_cv_kit.seed_scan_page(book_id, image=b'this is no image')
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        assert broken.scan_id is not None
        scan_id = broken.scan_id
        scan = await fx_cv_kit.databases.open(book_id).read(lambda repos: repos.scans.get(scan_id))
        full = BookKeys.scan_rendition(scan, scan.renditions.full)
        await fx_cv_kit.files.assets(book_id).delete_prefix(full)
        async with fx_cv_kit.files.assets(book_id).writable(full) as target:
            target.write_bytes(png_bytes(spread(PAGE_WIDTH_PX, HEIGHT_PX)))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(book_id, databases=fx_cv_kit.databases)
        versions = await versions_of(book_id, left, databases=fx_cv_kit.databases)
        expect((left.id, left.slot, right.slot) == (broken.id, Page.LEFT_HALF, Page.RIGHT_HALF))
        expect([version.state for version in versions] == [VersionState.READY])
        assert_expectations()

    async def test_the_step_cannot_be_previewed(self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Verify a preview of a step that makes pages fails its job with the reason and makes no page.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, [page] = await seed_spreads(fx_cv_kit)
        await fx_cv_kit.seed_base_version(book_id, page)
        preview = StepPreview(
            page_id=page.id, stage=Stage.PAGE_SPLIT, steps=(Step(processor_key=SPLIT_SPREAD),), step_index=0
        )
        job = await fx_cv_kit.service().start_preview(fx_open_book(actor, book_id), preview)
        await fx_cv_kit.work_queue()
        stored = stored_job(fx_cv_kit, job.id)
        expect(stored.state is JobState.FAILED)
        expect('cannot be previewed' in (stored.error or ''))
        expect(len(await book_of(book_id, databases=fx_open_book.databases)) == 1)
        assert_expectations()


class TestUnsplit:
    """Tests for running a step that does not split on a page that was split."""

    @staticmethod
    async def split_then_use_whole_scan(kit: ProcessingKit) -> tuple[Actor, BookId, list[Page]]:
        """Split the only spread of a book, and make the recipe of the stage ``split.none`` again.

        :param kit: The processing kit with the OpenCV plugins.
        :type kit: ProcessingKit
        :returns: The actor, the book and the two pages the split made, in book order.
        :rtype: tuple[Actor, BookRecord, list[Page]]
        """
        actor, book_id, _ = await seed_spreads(kit)
        await use_recipe(kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        pages = await book_of(book_id, databases=kit.databases)
        await use_recipe(kit, actor, book_id, Stage.PAGE_SPLIT, 'split.none')
        return actor, book_id, pages

    async def test_a_page_after_the_deleted_half_goes_stale_where_the_odd_pages_have_a_value(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify deleting the right half moves the next spread from the third place to the second.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first, second] = await seed_spreads(fx_cv_kit, count=2)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,)))
        await give_odd_pages_a_value(book_id, second, databases=fx_cv_kit.databases)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, 'split.none')
        await run_stage(
            fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,), confirm_unsplit=True)
        )
        assert (
            await stage_of(book_id, second, Stage.GEOMETRY, databases=fx_cv_kit.databases)
        ).state is StageState.STALE

    async def test_without_a_confirmation_the_page_fails_and_nothing_is_deleted(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify an unconfirmed run leaves the right half, and says on the stage of the left one that it failed.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [left, _right] = await self.split_then_use_whole_scan(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        expect(len(await book_of(book_id, databases=fx_cv_kit.databases)) == 2)
        expect(
            (await stage_of(book_id, left, Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state is StageState.FAILED
        )
        assert_expectations()

    async def test_a_replacement_that_fails_leaves_the_right_half_in_place(
        self, fx_cv_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a confirmed undoing whose new version cannot be made deletes nothing and keeps the split as it was.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param monkeypatch: Makes the writing of the page's files fail.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, book_id, [left, right] = await self.split_then_use_whole_scan(fx_cv_kit)
        halves = [await head_of(fx_cv_kit, book_id, page, Stage.PAGE_SPLIT) for page in (left, right)]
        monkeypatch.setattr(fx_cv_kit.writer, 'write', AsyncMock(side_effect=ConflictError('The disk is full.')))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, confirm_unsplit=True))
        pages = await book_of(book_id, databases=fx_cv_kit.databases)
        heads = [await head_of(fx_cv_kit, book_id, page, Stage.PAGE_SPLIT) for page in pages]
        expect([(page.id, page.slot) for page in pages] == [(left.id, Page.LEFT_HALF), (right.id, Page.RIGHT_HALF)])
        expect([head.id for head in heads] == [half.id for half in halves])
        assert_expectations()

    async def test_after_a_confirmation_the_right_half_is_deleted_with_its_files(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a confirmed run deletes the right page with its files, and makes the left one the whole scan.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [left, right] = await self.split_then_use_whole_scan(fx_cv_kit)
        right_version = await head_of(fx_cv_kit, book_id, right, Stage.PAGE_SPLIT)
        assert right_version.renditions is not None
        stored = BookKeys.version_rendition(right_version, right_version.renditions.full)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, confirm_unsplit=True))
        [remaining] = await book_of(book_id, databases=fx_cv_kit.databases)
        head = await head_of(fx_cv_kit, book_id, remaining, Stage.PAGE_SPLIT)
        expect((remaining.id, remaining.slot) == (left.id, Page.WHOLE_SCAN))
        expect((head.processor.key, head.state) == ('split.none', VersionState.READY))
        expect(
            (await stage_of(book_id, remaining, Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state
            is StageState.FRESH
        )
        with pytest.raises(NotFoundError):
            async with fx_cv_kit.files.assets(book_id).readable(stored):
                pass
        assert_expectations()


class SplitBook(NamedTuple):
    """The account, the book and the page of a scan of a spread, on which a change is made while a split runs.

    :ivar actor: Account owning the book.
    :ivar book_id: The identifier of the book.
    :ivar page: The page that shows the scan whole.
    """

    actor: Actor
    book_id: BookId
    page: Page


async def delete_the_spread(_kit: ProcessingKit, book: SplitBook) -> None:
    """Delete the page of the scan, as a request of the user does while a job splits it.

    :param _kit: What the processing services of the test share, which this change does not need.
    :type _kit: ProcessingKit
    :param book: The record of the book and the page.
    :type book: SplitBook
    """
    await _kit.databases.open(book.book_id).change(lambda repos, _book_record: repos.pages.delete(book.page.id))


async def split_the_scan_elsewhere(_kit: ProcessingKit, book: SplitBook) -> None:
    """Store the right half of the scan, as another run that split it first would have.

    :param _kit: What the processing services of the test share, which this change does not need.
    :type _kit: ProcessingKit
    :param book: The record of the book and the page.
    :type book: SplitBook
    """
    right = evolve(book.page, id=PageId(uuid4()), order_key=RIGHT_HALF_ORDER_KEY, slot=Page.RIGHT_HALF)
    await _kit.databases.open(book.book_id).change(lambda repos, _book_record: repos.pages.add(right))


async def draw_the_cut_line(kit: ProcessingKit, book: SplitBook) -> None:
    """Save the cut line of the step for the page, through the service the editor of the interface uses.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book: The account, the record of the book and the page.
    :type book: SplitBook
    """
    key = await kit.edit_key(book.book_id, book.page, Stage.PAGE_SPLIT, SPLIT_SPREAD)
    await kit.edits().save(
        open_for(book.actor, book.book_id),
        key,
        NewPageEdit(kind=CUT_LINE.editor, geometry=CUT_LINE),
        None,
    )


async def save_the_split_recipe(kit: ProcessingKit, book: SplitBook) -> None:
    """Save the recipe of the page split again, as the form of the recipe does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book: The account and the record of the book owning the recipe.
    :type book: SplitBook
    """
    await use_recipe(kit, book.actor, book.book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)


class TestSplitApplyStep:
    """Tests for the change that stores a split, which reads again what the split went by."""

    @pytest.mark.parametrize(
        ('change', 'pages_left'),
        [
            pytest.param(delete_the_spread, 0, id='page-deleted'),
            pytest.param(split_the_scan_elsewhere, 2, id='scan-split-by-another-run'),
            pytest.param(draw_the_cut_line, 1, id='cut-line-drawn-by-hand'),
            pytest.param(save_the_split_recipe, 1, id='recipe-saved'),
        ],
    )
    async def test_a_split_whose_basis_changed_meanwhile_is_skipped_and_stores_nothing(
        self,
        fx_cv_kit: ProcessingKit,
        monkeypatch: pytest.MonkeyPatch,
        change: Callable[[ProcessingKit, SplitBook], Awaitable[None]],
        pages_left: int,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a change that lands while the scan is cut stores no page, no version and no head, and no file.

        The change lands after the step ran and before the change that stores the halves. The job ends as succeeded
        with no page made, and the browser is told of no version.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the writer of renditions.
        :type monkeypatch: pytest.MonkeyPatch
        :param change: The change another request makes while the job runs.
        :type change: Callable[[ProcessingKit, SplitBook], Awaitable[None]]
        :param pages_left: How many pages the book has after the change, none of them made by the split.
        :type pages_left: int
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, [page] = await seed_spreads(fx_cv_kit)
        book = SplitBook(actor, book_id, page)
        await use_recipe(fx_cv_kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        job = await fx_cv_kit.service().start_run(
            fx_open_book(actor, book_id), Stage.PAGE_SPLIT, StageRun(stage=Stage.PAGE_SPLIT)
        )
        change_while_writing(fx_cv_kit, monkeypatch, partial(change, fx_cv_kit, book))
        await fx_cv_kit.work_queue()

        def read_what_is_stored(repos: BookRepositories) -> tuple[Slice[PageVersion], PageStage | None]:
            """Read the versions of the page split and its record, in one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The window of the versions of the stage, and the record of the stage or None.
            :rtype: tuple[Slice[PageVersion], PageStage | None]
            """
            return (
                repos.page_versions.list_for_stage(page.id, Stage.PAGE_SPLIT, None, EVERYTHING),
                repos.page_stages.find(PageStageKey(page.id, Stage.PAGE_SPLIT)),
            )

        versions, record = await fx_open_book.databases.open(book_id).read(read_what_is_stored)
        stored = stored_job(fx_cv_kit, job.id)
        told = [event for event in fx_cv_kit.events.published if isinstance(event, PageVersionReady)]
        expect((stored.state, stored.progress.total) == (JobState.SUCCEEDED, 1))
        expect(len(await book_of(book_id, databases=fx_open_book.databases)) == pages_left)
        expect(record is None)
        expect(versions.total == 0)
        expect(await fx_cv_kit.files.assets(book_id).size_of(BookKeys.page(page.id)) == 0)
        expect(told == [])
        assert_expectations()
