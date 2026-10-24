"""Tests for the automatic split of a book and the choice of one page or two, with the real OpenCV plugin.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Page
from bookreviver.domain.enums import ReviewReason, Stage, StageState
from bookreviver.domain.geometry import SplitChoice
from bookreviver.domain.values import NewPageEdit, StageRun
from tests.helpers.builders import open_for
from tests.helpers.samples import png_bytes
from tests.helpers.spreads import book_of, head_of, run_stage, stage_of
from tests.plugins.synthetic import draw_blank, draw_single_page, draw_spread

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import BookId
    from tests.helpers.builders import BookOpener
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

SPLIT_AUTO: str = 'split.auto'
# The slots of the pages of a book of a spread, a single page and a spread, once every scan is split by itself
WHOLE_BOOK_SLOTS: list[int] = [Page.LEFT_HALF, Page.RIGHT_HALF, Page.WHOLE_SCAN, Page.LEFT_HALF, Page.RIGHT_HALF]


async def seed_mixed_book(kit: ProcessingKit) -> tuple[Actor, BookId, list[Page]]:
    """Seed a book whose scans are a spread, a single page and a spread, each shown whole by one page.

    :param kit: The processing kit with the OpenCV plugins.
    :type kit: ProcessingKit
    :returns: The actor, the book and the three pages in book order.
    :rtype: tuple[Actor, BookRecord, list[Page]]
    """
    actor, book_id = await kit.seed_book()
    scans = [draw_spread().image, draw_single_page(), draw_spread(slant_deg=2.0).image]
    pages = [
        (await kit.seed_scan_page(book_id, order_key=f'a{number}', image=png_bytes(scan)))[0]
        for number, scan in enumerate(scans)
    ]
    return actor, book_id, pages


async def choose(kit: ProcessingKit, actor: Actor, book_id: BookId, page: Page, choice: SplitChoice) -> None:
    """Save the choice of one page or two for a scan, as the switch of the workspace does.

    :param kit: The processing kit with the OpenCV plugins.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: The identifier of the book.
    :type book_id: BookId
    :param page: The page that shows the scan whole, or its left half.
    :type page: Page
    :param choice: The decision.
    :type choice: SplitChoice
    """
    key = await kit.edit_key(book_id, page, Stage.PAGE_SPLIT, SPLIT_AUTO)
    await kit.edits().save(
        open_for(actor, book_id, databases=kit.databases, files=kit.files),
        key,
        NewPageEdit(kind=choice.editor, geometry=choice),
        None,
    )


async def run_over_every_page(kit: ProcessingKit, actor: Actor, book_id: BookId) -> None:
    """Run the page split over every page of the book by name, the right halves too.

    A run of the whole book leaves out the pages whose result is fresh, so a run that must go over pages that have one
    names them.

    :param kit: The processing kit with the OpenCV plugins.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_id: The identifier of the book.
    :type book_id: BookId
    """
    pages = await book_of(book_id, databases=kit.databases)
    await run_stage(kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=tuple(page.id for page in pages)))


class TestAutomaticSplit:
    """Tests for the run of the default recipe of the page split."""

    async def test_each_scan_becomes_one_page_or_two_by_itself(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify spreads are cut and the single page is kept whole, in one run over the book.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first, single, _last] = await seed_mixed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        pages = await book_of(book_id, databases=fx_cv_kit.databases)
        expect([page.slot for page in pages] == WHOLE_BOOK_SLOTS)
        expect(pages[0].id == first.id and pages[2].id == single.id)
        expect(all(page.scan_id is not None for page in pages))
        assert_expectations()

    async def test_a_second_run_makes_no_version_and_no_page(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a repeated run finds the versions of the spreads and of the single page alike.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, _ = await seed_mixed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        written = len(fx_cv_kit.writer.calls)
        await run_over_every_page(fx_cv_kit, actor, book_id)
        expect(len(fx_cv_kit.writer.calls) == written)
        expect(len(await book_of(book_id, databases=fx_cv_kit.databases)) == len(WHOLE_BOOK_SLOTS))
        assert_expectations()

    async def test_a_page_the_step_was_unsure_of_carries_the_reason(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a wide scan with no gutter is cut, and both halves are current with the reason to check them.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id = await fx_cv_kit.seed_book()
        await fx_cv_kit.seed_scan_page(book_id, image=png_bytes(draw_blank()))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        pages = await book_of(book_id, databases=fx_cv_kit.databases)
        heads = [await head_of(fx_cv_kit, book_id, page, Stage.PAGE_SPLIT) for page in pages]
        assert [head.review for head in heads] == [ReviewReason.UNSURE_GUTTER] * 2


class TestSplitChoice:
    """Tests for the decision of the user, which a run over the whole book does not undo."""

    async def test_one_page_for_a_spread_survives_a_run_on_all_pages(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a confirmed choice of one page undoes the split, and a later run on every page keeps the scan whole.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first, *_] = await seed_mixed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        await choose(fx_cv_kit, actor, book_id, first, SplitChoice(pages=SplitChoice.ONE_PAGE))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, confirm_unsplit=True))
        await run_over_every_page(fx_cv_kit, actor, book_id)
        pages = await book_of(book_id, databases=fx_cv_kit.databases)
        expect([page.slot for page in pages] == [Page.WHOLE_SCAN, Page.WHOLE_SCAN, Page.LEFT_HALF, Page.RIGHT_HALF])
        expect(
            (await stage_of(book_id, pages[0], Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state
            is StageState.FRESH
        )
        assert_expectations()

    async def test_without_a_confirmation_the_choice_of_one_page_fails_and_deletes_nothing(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify going back to one page needs the confirmation, as undoing a split by another recipe does.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [first, *_] = await seed_mixed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        await choose(fx_cv_kit, actor, book_id, first, SplitChoice(pages=SplitChoice.ONE_PAGE))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(first.id,)))
        expect(len(await book_of(book_id, databases=fx_cv_kit.databases)) == len(WHOLE_BOOK_SLOTS))
        expect(
            (await stage_of(book_id, first, Stage.PAGE_SPLIT, databases=fx_cv_kit.databases)).state is StageState.FAILED
        )
        assert_expectations()

    async def test_two_pages_for_a_single_page_survives_a_run_on_all_pages(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a choice of two pages cuts a single scan, and a later run on every page keeps it cut.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, [_first, single, _last] = await seed_mixed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        await choose(fx_cv_kit, actor, book_id, single, SplitChoice(pages=SplitChoice.TWO_PAGES))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, page_ids=(single.id,)))
        await run_over_every_page(fx_cv_kit, actor, book_id)
        pages = await book_of(book_id, databases=fx_cv_kit.databases)
        expect([page.slot for page in pages] == [1, 2, 1, 2, 1, 2])
        assert_expectations()

    async def test_deleting_the_choice_returns_the_decision_to_the_automatic_split(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the page returns to one page when its choice of two is deleted, after the confirmation.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, [_first, single, _last] = await seed_mixed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        await choose(fx_cv_kit, actor, book_id, single, SplitChoice(pages=SplitChoice.TWO_PAGES))
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
        await fx_cv_kit.edits().delete(
            fx_open_book(actor, book_id),
            await fx_cv_kit.edit_key(book_id, single, Stage.PAGE_SPLIT, SPLIT_AUTO),
        )
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT, confirm_unsplit=True))
        pages = await book_of(book_id, databases=fx_open_book.databases)
        expect([page.slot for page in pages] == WHOLE_BOOK_SLOTS)
        assert_expectations()
