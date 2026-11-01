"""Tests for Margins on a whole book, with the real OpenCV plugins: one page size and the work of the user on a page.

The sheets are scans with a block of text of another size on each, as the pages of a book without Select content are.
The recipe is the one step, Margins, with the parameters a book starts with, so the page size is 0 and no press of
"Measure the book" gives it a value. The tests need OpenCV, and are skipped with the reason where the optional group
``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.geometry import ContentBox, Rect, TransformKind
from bookreviver.domain.jobs import Progress, StageRun
from bookreviver.domain.margins import NOMINAL_BLOCK_MM, NormalizeParam
from bookreviver.domain.page_steps import NewPageEdit
from bookreviver.domain.recipes import RecipeDraft, Step
from bookreviver.domain.spans import SpanName
from bookreviver.domain.stages import Stage
from bookreviver.domain.versions import VersionData
from tests.harness.builders.page_batches import PageValues
from tests.harness.builders.processing import stored_job
from tests.harness.builders.spreads import book_of, head_of, run_stage, use_recipe
from tests.harness.images.samples import PAPER, png_bytes, text_page

if TYPE_CHECKING:
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page
    from bookreviver.domain.versions import PageVersion
    from tests.harness.builders.domain import BookOpener
    from tests.harness.builders.processing import ProcessingKit

pytestmark = pytest.mark.anyio

NORMALIZE: str = 'geometry.normalize'
CROP: str = 'geometry.crop'
SPLIT_NONE: str = 'split.none'
SHEET_SIZE_PX: tuple[int, int] = (900, 1_200)
# The size of the text page each block is cut from, and where the block lies on its sheet, for the three pages
BLOCKS: tuple[tuple[tuple[int, int], tuple[int, int]], ...] = (
    ((500, 700), (120, 150)),
    ((700, 900), (100, 100)),
    ((560, 760), (200, 180)),
)
# The page whose block is the largest, and the one a test changes
LARGEST: int = 1
CHANGED: int = 0
# How far a measure by the box may be from the pixels a block takes, which keep a little paper round the ink
BOX_TOLERANCE_PX: float = 12.0
MARGIN_TOP_BY_HAND: float = 30.0
# The versions a page makes in a run of the book by one step that leaves its size to the book: the measuring pass, and
# the run by the size
TASKS_PER_PAGE: int = 2
MANUAL_BOX: ContentBox = ContentBox(left=150, top=170, width=200, height=300)
# A box wider and taller than the block of every page, which makes the page of the book larger
LARGE_BOX: ContentBox = ContentBox(left=40, top=60, width=820, height=1_080)
# The frame of Select content that the user gave, which is smaller than the block of the page that is changed
HAND_FRAME: Rect = Rect(left=130, top=160, width=140, height=420)


def sheet(index: int) -> Image.Image:
    """Draw the sheet of a page: paper with a block of text on it, whose size and place are those of the page.

    :param index: Number of the page in the book.
    :type index: int
    :returns: The gray sheet.
    :rtype: Image.Image
    """
    size, place = BLOCKS[index]
    page = text_page(*size)
    ink = Image.eval(page, lambda value: PAPER - value)
    block = page.crop(ink.getbbox())
    sheet_of_paper = Image.new('L', SHEET_SIZE_PX, PAPER)
    sheet_of_paper.paste(block, place)
    return sheet_of_paper


async def seed_book(kit: ProcessingKit) -> tuple[Actor, BookId, list[Page]]:
    """Seed a book of the sheets, shown whole by the page split, with Margins as the one step of the Geometry recipe.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book and the pages in book order.
    :rtype: tuple[Actor, BookRecord, list[Page]]
    """
    actor, book_id = await kit.seed_book()
    for index in range(len(BLOCKS)):
        await kit.seed_scan_page(book_id, order_key=f'a{index}', image=png_bytes(sheet(index)))
    await use_recipe(kit, actor, book_id, Stage.PAGE_SPLIT, SPLIT_NONE)
    await run_stage(kit, actor, book_id, StageRun(stage=Stage.PAGE_SPLIT))
    await use_recipe(kit, actor, book_id, Stage.GEOMETRY, NORMALIZE)
    return actor, book_id, await book_of(book_id, databases=kit.databases)


async def margins_of(kit: ProcessingKit, book_id: BookId, page: Page) -> PageVersion:
    """Read the version Margins made on a page, which is the current one of the Geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :returns: The version.
    :rtype: PageVersion
    """
    return await head_of(kit, book_id, page, Stage.GEOMETRY)


def size_of(version: PageVersion) -> tuple[int, int]:
    """Read the size of the page a version holds.

    :param version: A version of Margins.
    :type version: PageVersion
    :returns: Width and height in pixels.
    :rtype: tuple[int, int]
    """
    return int(version.data[VersionData.WIDTH_PX]), int(version.data[VersionData.HEIGHT_PX])


class TestOnePageSizeForTheBook:
    """Tests for the page size that is by the book, which needs no measure of the book to be one for every page."""

    async def test_a_run_on_all_pages_gives_every_page_one_size_without_a_measure(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify pages of three block sizes come out of one size, which holds the largest block and its margins.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        versions = [await margins_of(fx_cv_kit, book_id, page) for page in pages]
        sizes = {size_of(version) for version in versions}
        largest = Rect.from_data(versions[LARGEST].data[VersionData.CONTENT_BOX])
        params = (await fx_cv_kit.recipe_of(actor, book_id, Stage.GEOMETRY)).steps[0].params
        width, height = next(iter(sizes))
        expect(len(sizes) == 1)
        # The margins of a book that is not measured are the ones the step starts with, in millimetres of a block that is
        # taken for NOMINAL_BLOCK_MM wide, since the sheets carry no resolution
        pixels_per_mm = largest.width / NOMINAL_BLOCK_MM
        sides = params[NormalizeParam.MARGIN_INNER] + params[NormalizeParam.MARGIN_OUTER]
        vertical = params[NormalizeParam.MARGIN_TOP] + params[NormalizeParam.MARGIN_BOTTOM]
        expect(abs(width - (largest.width + sides * pixels_per_mm)) <= BOX_TOLERANCE_PX)
        expect(abs(height - (largest.height + vertical * pixels_per_mm)) <= BOX_TOLERANCE_PX)
        # The size is not written into the recipe, so it follows the pages when they change
        expect(params[NormalizeParam.PAGE_WIDTH] == 0 and params[NormalizeParam.PAGE_HEIGHT] == 0)
        assert_expectations()

    async def test_progress_counts_the_pass_that_measures_the_book_as_tasks(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a run of three pages plans a task for each step it makes, the first pass that measures them included.

        A page whose step leaves the page size to the book is taken as far as that step once to find its box, and then
        run by the size of the book, so every page makes two versions and the run has twice as many tasks as pages.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        fx_cv_kit.tracer.finished.clear()
        job = await fx_cv_kit.service().start_run(
            fx_open_book(actor, book_id), Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY)
        )
        await fx_cv_kit.work_queue()
        tasks = len(pages) * TASKS_PER_PAGE
        expect(stored_job(fx_cv_kit, job.id).progress == Progress(done=tasks, total=tasks))
        expect(len(fx_cv_kit.tracer.named(SpanName.STEP)) == tasks)
        assert_expectations()

    async def test_the_block_is_placed_and_not_the_sheet_it_lies_on(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a recipe with no Select content records the box of the block, which is far smaller than the sheet.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        for index, page in enumerate(pages):
            box = Rect.from_data((await margins_of(fx_cv_kit, book_id, page)).data[VersionData.CONTENT_BOX])
            (size_x, _), (place_x, place_y) = BLOCKS[index]
            expect(abs(box.left - place_x) <= BOX_TOLERANCE_PX and abs(box.top - place_y) <= BOX_TOLERANCE_PX)
            # The text page the block was cut from is narrower than the sheet, and the block narrower than that page
            expect(box.width <= size_x)
        assert_expectations()

    async def test_a_page_run_alone_takes_the_size_of_the_book(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a page whose box was made smaller and that is run alone stays as large as the pages round it.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        before = size_of(await margins_of(fx_cv_kit, book_id, pages[CHANGED]))
        key = await fx_cv_kit.edit_key(book_id, pages[CHANGED], Stage.GEOMETRY, NORMALIZE)
        await fx_cv_kit.edits().save(
            fx_open_book(actor, book_id), key, NewPageEdit(kind=ContentBox.editor, geometry=MANUAL_BOX), None
        )
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY, page_ids=(pages[CHANGED].id,)))
        changed = await margins_of(fx_cv_kit, book_id, pages[CHANGED])
        expect(size_of(changed) == before)
        expect(size_of(changed) == size_of(await margins_of(fx_cv_kit, book_id, pages[LARGEST])))
        assert_expectations()


class TestWhatTheUserSetsOnAPage:
    """Tests for the box and the margin a user gave to one page, which a run of every page keeps."""

    async def test_a_box_and_a_margin_set_on_a_page_outlive_a_run_on_all_pages(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the run uses the box of the user and the margin the page changed, and the other pages use neither.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        key = await fx_cv_kit.edit_key(book_id, pages[CHANGED], Stage.GEOMETRY, NORMALIZE)
        await fx_cv_kit.edits().save(
            fx_open_book(actor, book_id), key, NewPageEdit(kind=ContentBox.editor, geometry=MANUAL_BOX), None
        )
        await PageValues(fx_cv_kit, actor, book_id).set(key, NormalizeParam.MARGIN_TOP.value, MARGIN_TOP_BY_HAND)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        changed = await margins_of(fx_cv_kit, book_id, pages[CHANGED])
        other = await margins_of(fx_cv_kit, book_id, pages[LARGEST])
        expect(Rect.from_data(changed.data[VersionData.CONTENT_BOX]) == Rect(**MANUAL_BOX.to_data()))
        expect(changed.params[NormalizeParam.MARGIN_TOP] == MARGIN_TOP_BY_HAND)
        expect(other.params[NormalizeParam.MARGIN_TOP] != MARGIN_TOP_BY_HAND)
        expect(size_of(changed) == size_of(other))
        expect(await fx_cv_kit.edits().list(fx_open_book(actor, book_id), pages[CHANGED].id, Stage.GEOMETRY))
        assert_expectations()

    async def test_a_small_box_of_the_user_leaves_the_pages_of_the_book_of_one_size(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a box the user made small is placed on the page of the book, which the largest block still decides.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        key = await fx_cv_kit.edit_key(book_id, pages[CHANGED], Stage.GEOMETRY, NORMALIZE)
        await fx_cv_kit.edits().save(
            fx_open_book(actor, book_id), key, NewPageEdit(kind=ContentBox.editor, geometry=MANUAL_BOX), None
        )
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        versions = [await margins_of(fx_cv_kit, book_id, page) for page in pages]
        largest = Rect.from_data(versions[LARGEST].data[VersionData.CONTENT_BOX])
        expect(len({size_of(version) for version in versions}) == 1)
        expect(size_of(versions[CHANGED])[0] > largest.width)
        assert_expectations()

    async def test_a_box_set_after_a_run_resizes_the_pages_whose_result_was_fresh(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a box larger than every block, set on one page after a run, gives the next run one size for the book.

        The pages the box was not set on keep a fresh result of the first run, made by the old size of the book, so a run
        that leaves them out as fresh would leave the book of two sizes.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages = await seed_book(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        before = size_of(await margins_of(fx_cv_kit, book_id, pages[LARGEST]))
        key = await fx_cv_kit.edit_key(book_id, pages[CHANGED], Stage.GEOMETRY, NORMALIZE)
        await fx_cv_kit.edits().save(
            fx_open_book(actor, book_id), key, NewPageEdit(kind=ContentBox.editor, geometry=LARGE_BOX), None
        )
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        sizes = [size_of(await margins_of(fx_cv_kit, book_id, page)) for page in pages]
        expect(len(set(sizes)) == 1, f'The sizes of the pages are {sizes}')
        expect(sizes[LARGEST][0] > before[0], f'The book was {before} before the box, and is {sizes[LARGEST]} after')
        assert_expectations()


class TestMarginsAfterSelectContent:
    """Tests for Margins with Select content before it, which leaves the page as it is, run as a stage runs them."""

    @staticmethod
    async def seed_two_steps(kit: ProcessingKit) -> tuple[Actor, BookId, list[Page]]:
        """Seed the book of the sheets with Select content and Margins as the two steps of the Geometry recipe.

        :param kit: What the processing services of the test share.
        :type kit: ProcessingKit
        :returns: The actor, the book and the pages in book order.
        :rtype: tuple[Actor, BookRecord, list[Page]]
        """
        actor, book_id, pages = await seed_book(kit)
        draft = RecipeDraft(steps=[Step(processor_key=CROP), Step(processor_key=NORMALIZE)])
        await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, draft)
        return actor, book_id, pages

    async def test_a_step_before_margins_does_not_take_the_size_of_the_book(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the size by the book is laid over Margins alone, so the step before it runs and the pages share a size.

        The step before Margins has no field for a page size, and a run that laid the size over it would fail.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, pages = await TestMarginsAfterSelectContent.seed_two_steps(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        versions = [await margins_of(fx_cv_kit, book_id, page) for page in pages]
        expect(all(version.processor.key == NORMALIZE for version in versions))
        expect(len({size_of(version) for version in versions}) == 1)
        assert_expectations()

    async def test_a_run_on_all_pages_places_the_block_select_content_found_on_the_whole_sheet(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify Select content hands on the sheet whole, and Margins cuts the block out of it by the recorded frame.

        The book is run as a whole, so the size by the book is laid over Margins and over no other step.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_id, pages = await TestMarginsAfterSelectContent.seed_two_steps(fx_cv_kit)
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        for index, page in enumerate(pages):
            margins = await margins_of(fx_cv_kit, book_id, page)
            assert margins.input_id is not None
            select = await fx_cv_kit.stored_version(book_id, margins.input_id)
            frame = Rect.from_data(select.data[VersionData.FRAME])
            box = Rect.from_data(margins.data[VersionData.CONTENT_BOX])
            (size_x, _), (place_x, place_y) = BLOCKS[index]
            expect(select.transform.kind is TransformKind.IDENTITY)
            expect((select.data[VersionData.WIDTH_PX], select.data[VersionData.HEIGHT_PX]) == SHEET_SIZE_PX)
            expect(abs(box.left - frame.left) <= 1 and abs(box.width - frame.width) <= 2)
            expect(abs(box.left - place_x) <= BOX_TOLERANCE_PX and abs(box.top - place_y) <= BOX_TOLERANCE_PX)
            expect(box.width <= size_x)
            expect(size_of(margins) != SHEET_SIZE_PX)
        expect(len({size_of(await margins_of(fx_cv_kit, book_id, page)) for page in pages}) == 1)
        assert_expectations()

    async def test_the_frame_the_user_gave_to_select_content_is_the_box_margins_places(
        self, fx_cv_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a frame set by hand on Select content is cut out of the whole sheet and placed by Margins.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id, pages = await TestMarginsAfterSelectContent.seed_two_steps(fx_cv_kit)
        key = await fx_cv_kit.edit_key(book_id, pages[CHANGED], Stage.GEOMETRY, CROP)
        await fx_cv_kit.edits().save(
            fx_open_book(actor, book_id), key, NewPageEdit(kind=Rect.editor, geometry=HAND_FRAME), None
        )
        await run_stage(fx_cv_kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
        margins = await margins_of(fx_cv_kit, book_id, pages[CHANGED])
        assert margins.input_id is not None
        select = await fx_cv_kit.stored_version(book_id, margins.input_id)
        box = Rect.from_data(margins.data[VersionData.CONTENT_BOX])
        expect(Rect.from_data(select.data[VersionData.FRAME]) == HAND_FRAME)
        expect(abs(box.left - HAND_FRAME.left) <= 1 and abs(box.top - HAND_FRAME.top) <= 1)
        expect(abs(box.width - HAND_FRAME.width) <= 2 and abs(box.height - HAND_FRAME.height) <= 2)
        assert_expectations()
