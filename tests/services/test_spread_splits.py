"""Tests for splitting a scan into the two pages of a spread, and undoing the split, with the real OpenCV plugin.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

import io
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.entities import Page
from bookreviver.domain.enums import JobState, PageChange, Stage, StageState, VersionState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PagesChanged, PageVersionReady
from bookreviver.domain.geometry import Line, Point
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPageEdit, StageRun, Step, StepPreview
from tests.helpers.samples import png_bytes, spread
from tests.helpers.spreads import (
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

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# Where the cut line the user draws crosses the top and the bottom of the scan
CUT_LINE: Line = Line(start=Point(x=880, y=0), end=Point(x=900, y=HEIGHT_PX - 1))


async def width_of(kit: ProcessingKit, project: Project, version: PageVersion) -> int:
    """Read the width of the stored ``full`` image of a version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the version's page.
    :type project: Project
    :param version: A ready version that has its files.
    :type version: PageVersion
    :returns: The width in pixels of the image the version stored.
    :rtype: int
    """
    assert version.renditions is not None
    async with kit.assets.readable(ProjectKeys(project.id).version_rendition(version, version.renditions.full)) as full:
        with Image.open(io.BytesIO(full.read_bytes())) as image:
            return image.width


class TestSplit:
    """Tests for the run of a recipe whose step splits a scan."""

    async def test_the_page_stays_the_left_half_and_the_right_half_follows_it(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a new page is inserted right after the page that was split, and the pages after it keep their order.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, [first, second] = await seed_spreads(fx_cv_kit, count=2)
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,)))
        pages = await book_of(fx_cv_kit, project)
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
        actor, project, [first] = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(fx_cv_kit, project)
        left_version = await head_of(fx_cv_kit, left, Stage.PAGE_SPLIT)
        right_version = await head_of(fx_cv_kit, right, Stage.PAGE_SPLIT)
        expect(left.id == first.id)
        for version in (left_version, right_version):
            expect((version.state, version.processor.key, version.input_id) == (VersionState.READY, SPLIT_SPREAD, None))
            expect(version.tiles_ready)
        expect(
            await width_of(fx_cv_kit, project, left_version) + await width_of(fx_cv_kit, project, right_version)
            == 2 * PAGE_WIDTH_PX
        )
        expect((await stage_of(fx_cv_kit, right, Stage.PAGE_SPLIT)).state is StageState.FRESH)
        expect((await stage_of(fx_cv_kit, left, Stage.PAGE_SPLIT)).state is StageState.FRESH)
        assert_expectations()

    async def test_the_events_name_the_new_page_and_both_versions(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the browser is told of the page that was added and of the two versions that became ready.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, _ = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        _left, right = await book_of(fx_cv_kit, project)
        published = fx_cv_kit.events.published
        added = [event for event in published if isinstance(event, PagesChanged) and event.change is PageChange.ADDED]
        ready = [event.version.page_id for event in published if isinstance(event, PageVersionReady)]
        expect([event.page_ids for event in added] == [[right.id]])
        expect(len(ready) == 2)
        assert_expectations()

    async def test_a_second_run_with_the_same_line_finds_the_versions_and_adds_no_page(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a repeated run runs no step, makes no version and no page.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, _ = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        written = len(fx_cv_kit.writer.calls)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        expect(len(fx_cv_kit.writer.calls) == written)
        expect(len(await book_of(fx_cv_kit, project)) == 2)
        assert_expectations()

    async def test_a_run_over_the_whole_book_leaves_the_right_halves_to_their_left_halves(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the right half made by a run is not split again by the next run over every page.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, _ = await seed_spreads(fx_cv_kit, count=2)
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        for _ in range(2):
            await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        expect([page.slot for page in await book_of(fx_cv_kit, project)] == [1, 2, 1, 2])
        assert_expectations()

    async def test_a_new_cut_line_makes_new_base_versions_of_both_halves_beside_the_old_ones(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify an edit of the line is read by the next run, and the versions of the old line are not deleted.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, _ = await seed_spreads(fx_cv_kit)
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(fx_cv_kit, project)
        before = [await head_of(fx_cv_kit, page, Stage.PAGE_SPLIT) for page in (left, right)]
        key = await fx_cv_kit.edit_key(left, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await fx_cv_kit.edits().save(actor, project.id, key, NewPageEdit(kind=CUT_LINE.editor, geometry=CUT_LINE), None)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        after = [await head_of(fx_cv_kit, page, Stage.PAGE_SPLIT) for page in (left, right)]
        every = [await fx_cv_kit.uow().page_versions.list_for_page(page.id) for page in (left, right)]
        expect(all(new.id != old.id for new, old in zip(after, before, strict=True)))
        expect([len(versions) for versions in every] == [2, 2])
        expect(len(await book_of(fx_cv_kit, project)) == 2)
        assert_expectations()

    async def test_a_split_that_fails_leaves_the_book_as_it_was(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify no page is added and the page keeps its slot when the step fails, which the page's version says why.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        page, _ = await fx_cv_kit.seed_scan_page(project, image=b'this is no image')
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        [stored] = await book_of(fx_cv_kit, project)
        versions = await fx_cv_kit.uow().page_versions.list_for_page(page.id)
        added = [event for event in fx_cv_kit.events.published if isinstance(event, PagesChanged)]
        expect((stored.id, stored.slot) == (page.id, Page.WHOLE_SCAN))
        expect(
            [(version.state, version.processor.key) for version in versions] == [(VersionState.FAILED, SPLIT_SPREAD)]
        )
        expect('cannot be read' in versions[0].data['error'])
        expect((await stage_of(fx_cv_kit, page, Stage.PAGE_SPLIT)).state is StageState.FAILED)
        expect(not added)
        assert_expectations()

    async def test_a_split_that_failed_is_made_again_by_the_next_run(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the failed version is replaced when the scan can be read, and then the page has its right half.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        broken, _ = await fx_cv_kit.seed_scan_page(project, image=b'this is no image')
        await use_recipe(fx_cv_kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        keys = ProjectKeys(project.id)
        assert broken.scan_id is not None
        scan = await fx_cv_kit.uow().scans.get(broken.scan_id)
        full = keys.scan_rendition(scan, scan.renditions.full)
        await fx_cv_kit.assets.delete_prefix(full)
        async with fx_cv_kit.assets.writable(full) as target:
            target.write_bytes(png_bytes(spread(PAGE_WIDTH_PX, HEIGHT_PX)))
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        left, right = await book_of(fx_cv_kit, project)
        versions = await fx_cv_kit.uow().page_versions.list_for_page(left.id)
        expect((left.id, left.slot, right.slot) == (broken.id, Page.LEFT_HALF, Page.RIGHT_HALF))
        expect([version.state for version in versions] == [VersionState.READY])
        assert_expectations()

    async def test_the_step_cannot_be_previewed(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a preview of a step that makes pages fails its job with the reason and makes no page.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, [page] = await seed_spreads(fx_cv_kit)
        await fx_cv_kit.seed_base_version(page)
        preview = StepPreview(
            page_id=page.id, stage=Stage.PAGE_SPLIT, steps=(Step(processor_key=SPLIT_SPREAD),), step_index=0
        )
        job = await fx_cv_kit.service().start_preview(actor, project.id, preview)
        await fx_cv_kit.jobs().preview_step(job.id)
        stored = await fx_cv_kit.uow().jobs.get(job.id)
        expect(stored.state is JobState.FAILED)
        expect('cannot be previewed' in (stored.error or ''))
        expect(len(await book_of(fx_cv_kit, project)) == 1)
        assert_expectations()


class TestUnsplit:
    """Tests for running a step that does not split on a page that was split."""

    @staticmethod
    async def split_then_use_whole_scan(kit: ProcessingKit) -> tuple[Actor, Project, list[Page]]:
        """Split the only spread of a project, and make the recipe of the stage ``split.none`` again.

        :param kit: The processing kit with the OpenCV plugins.
        :type kit: ProcessingKit
        :returns: The actor, the project and the two pages the split made, in book order.
        :rtype: tuple[Actor, Project, list[Page]]
        """
        actor, project, _ = await seed_spreads(kit)
        await use_recipe(kit, actor, project, Stage.PAGE_SPLIT, SPLIT_SPREAD)
        await run_stage(kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        pages = await book_of(kit, project)
        await use_recipe(kit, actor, project, Stage.PAGE_SPLIT, 'split.none')
        return actor, project, pages

    async def test_without_a_confirmation_the_page_fails_and_nothing_is_deleted(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify an unconfirmed run leaves the right half, and says on the stage of the left one that it failed.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, [left, _right] = await self.split_then_use_whole_scan(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        expect(len(await book_of(fx_cv_kit, project)) == 2)
        expect((await stage_of(fx_cv_kit, left, Stage.PAGE_SPLIT)).state is StageState.FAILED)
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
        actor, project, [left, right] = await self.split_then_use_whole_scan(fx_cv_kit)
        halves = [await head_of(fx_cv_kit, page, Stage.PAGE_SPLIT) for page in (left, right)]
        monkeypatch.setattr(fx_cv_kit.writer, 'write', AsyncMock(side_effect=ConflictError('The disk is full.')))
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT, confirm_unsplit=True))
        pages = await book_of(fx_cv_kit, project)
        heads = [await head_of(fx_cv_kit, page, Stage.PAGE_SPLIT) for page in pages]
        expect([(page.id, page.slot) for page in pages] == [(left.id, Page.LEFT_HALF), (right.id, Page.RIGHT_HALF)])
        expect([head.id for head in heads] == [half.id for half in halves])
        assert_expectations()

    async def test_after_a_confirmation_the_right_half_is_deleted_with_its_files(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a confirmed run deletes the right page, makes the left one the whole scan, and tells the browser.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project, [left, right] = await self.split_then_use_whole_scan(fx_cv_kit)
        right_version = await head_of(fx_cv_kit, right, Stage.PAGE_SPLIT)
        assert right_version.renditions is not None
        stored = ProjectKeys(project.id).version_rendition(right_version, right_version.renditions.full)
        await run_stage(fx_cv_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT, confirm_unsplit=True))
        [remaining] = await book_of(fx_cv_kit, project)
        head = await head_of(fx_cv_kit, remaining, Stage.PAGE_SPLIT)
        removed = [
            event
            for event in fx_cv_kit.events.published
            if isinstance(event, PagesChanged) and event.change is PageChange.REMOVED
        ]
        expect((remaining.id, remaining.slot) == (left.id, Page.WHOLE_SCAN))
        expect((head.processor.key, head.state) == ('split.none', VersionState.READY))
        expect((await stage_of(fx_cv_kit, remaining, Stage.PAGE_SPLIT)).state is StageState.FRESH)
        expect([event.page_ids for event in removed] == [[right.id]])
        with pytest.raises(NotFoundError):
            async with fx_cv_kit.assets.readable(stored):
                pass
        assert_expectations()
