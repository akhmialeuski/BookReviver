"""Tests for the cleanup.eraser processor, on pages and brush masks drawn for the tests.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    EraserFill,
    ProcessorScope,
    ReviewReason,
    Stage,
    VersionData,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import save
from tests.plugins.cleanup_pages import INK, PAPER, read_gray, run_step

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor

PAGE_NAME: str = 'page.png'
MASK_NAME: str = 'mask.png'
KEY_PATTERN: str = r'cleanup\.eraser'
FILL: str = 'fill'
OUTSIDE: str = 'fill_outside_frame'
PAGE_PX: tuple[int, int] = (400, 300)
# The tone of the page of the tests, the area the user brushes (rows, columns) and the mark inside it
TONE: int = 150
BRUSHED_ROWS: slice = slice(100, 140)
BRUSHED_COLUMNS: slice = slice(120, 200)
# The frame of the content in the pixels of the page: left, top, width and height
FRAME: dict[str, float] = {'left': 100.0, 'top': 80.0, 'width': 200.0, 'height': 140.0}


def _page(tmp_path: Path, *, tone: int = TONE) -> Path:
    """Save a page of one tone, with a black mark inside the area that will be brushed.

    :param tmp_path: Directory to save into.
    :type tmp_path: Path
    :param tone: Tone of the page.
    :type tone: int
    :returns: Path of the page.
    :rtype: Path
    """
    width, height = PAGE_PX
    page = np.full((height, width), tone, dtype=np.uint8)
    page[BRUSHED_ROWS.start + 10 : BRUSHED_ROWS.start + 20, BRUSHED_COLUMNS.start + 10 : BRUSHED_COLUMNS.start + 20] = (
        INK
    )
    return save(Image.fromarray(page), tmp_path / PAGE_NAME)


def _mask(tmp_path: Path, *, size: tuple[int, int] = PAGE_PX, scale: float = 1.0) -> Path:
    """Save the mask of a brush edit: white over the brushed area, black elsewhere.

    :param tmp_path: Directory to save into.
    :type tmp_path: Path
    :param size: Width and height of the page the mask was brushed on.
    :type size: tuple[int, int]
    :param scale: Size of the mask over the size of the page.
    :type scale: float
    :returns: Path of the mask.
    :rtype: Path
    """
    width, height = size
    mask = np.zeros((round(height * scale), round(width * scale)), dtype=np.uint8)
    mask[
        round(BRUSHED_ROWS.start * scale) : round(BRUSHED_ROWS.stop * scale),
        round(BRUSHED_COLUMNS.start * scale) : round(BRUSHED_COLUMNS.stop * scale),
    ] = PAPER
    return save(Image.fromarray(mask), tmp_path / MASK_NAME)


class TestEraser:
    """Tests for Eraser."""

    def test_spec_is_the_manual_step_of_the_cleanup_stage_with_the_brush_editor(self, fx_eraser: Processor) -> None:
        """Verify the spec: the key, the stage, the page scope and the editor.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        """
        spec = fx_eraser.spec
        expect(spec.key == 'cleanup.eraser')
        expect(spec.stage is Stage.CLEANUP)
        expect(spec.scope is ProcessorScope.PAGE)
        expect(spec.editor is EditorKind.BRUSH_MASK)
        assert_expectations()

    @pytest.mark.parametrize(
        ('fill', 'expected'), [(EraserFill.WHITE, PAPER), (EraserFill.BLACK, INK), (EraserFill.AROUND, TONE)]
    )
    def test_the_brushed_area_is_painted_with_the_colour_that_was_chosen(
        self, fx_eraser: Processor, tmp_path: Path, fill: EraserFill, expected: int
    ) -> None:
        """Verify white, black and the mean colour round the area, and that the rest of the page is untouched.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param fill: The colour chosen.
        :type fill: EraserFill
        :param expected: The tone the brushed area comes out in.
        :type expected: int
        """
        output = run_step(fx_eraser, _page(tmp_path), tmp_path, {FILL: fill, OUTSIDE: False}, mask=_mask(tmp_path))
        assert output.image is not None
        erased = read_gray(output.image)
        expect(bool((erased[BRUSHED_ROWS, BRUSHED_COLUMNS] == expected).all()))
        expect(int(erased[0, 0]) == TONE)
        expect(int(erased[-1, -1]) == TONE)
        expect(output.review is None)
        assert_expectations()

    def test_the_mean_colour_is_taken_round_the_area_and_not_from_the_whole_page(
        self, fx_eraser: Processor, tmp_path: Path
    ) -> None:
        """Verify an area on a dark band is painted with the tone of the band, not with the tone of the page.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        width, height = PAGE_PX
        page = np.full((height, width), PAPER, dtype=np.uint8)
        page[
            BRUSHED_ROWS.start - 30 : BRUSHED_ROWS.stop + 30, BRUSHED_COLUMNS.start - 30 : BRUSHED_COLUMNS.stop + 30
        ] = TONE
        image = save(Image.fromarray(page), tmp_path / PAGE_NAME)
        output = run_step(fx_eraser, image, tmp_path, {FILL: EraserFill.AROUND, OUTSIDE: False}, mask=_mask(tmp_path))
        assert output.image is not None
        assert bool((read_gray(output.image)[BRUSHED_ROWS, BRUSHED_COLUMNS] == TONE).all())

    def test_a_mask_of_another_size_is_resized_to_the_page(self, fx_eraser: Processor, tmp_path: Path) -> None:
        """Verify the mask brushed on the full page erases the same area of a preview that is half its size.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        width, height = PAGE_PX
        small = np.full((height // 2, width // 2), TONE, dtype=np.uint8)
        image = save(Image.fromarray(small), tmp_path / PAGE_NAME)
        output = run_step(
            fx_eraser, image, tmp_path, {FILL: EraserFill.WHITE, OUTSIDE: False}, mask=_mask(tmp_path), scale=0.5
        )
        assert output.image is not None
        erased = read_gray(output.image)
        expect(
            bool(
                (
                    erased[
                        BRUSHED_ROWS.start // 2 + 2 : BRUSHED_ROWS.stop // 2 - 2,
                        BRUSHED_COLUMNS.start // 2 + 2 : BRUSHED_COLUMNS.stop // 2 - 2,
                    ]
                    == PAPER
                ).all()
            )
        )
        expect(int(erased[0, 0]) == TONE)
        assert_expectations()

    def test_everything_outside_the_frame_is_made_white(self, fx_eraser: Processor, tmp_path: Path) -> None:
        """Verify the margins outside the frame of the crop are white and the content inside it is not touched.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        output = run_step(fx_eraser, _page(tmp_path), tmp_path, facts={VersionData.CONTENT_FRAME: FRAME})
        assert output.image is not None
        erased = read_gray(output.image)
        inside = erased[80:220, 100:300]
        expect(int(erased[0, 0]) == PAPER)
        expect(int(erased[-1, -1]) == PAPER)
        expect(int(erased[79, 150]) == PAPER)
        expect(bool((inside == TONE).sum() > 0.9 * inside.size))
        expect(output.review is None)
        expect(output.data[VersionData.CONTENT_FRAME] == FRAME)
        assert_expectations()

    def test_the_frame_of_the_crop_is_read_when_no_step_of_the_cleanup_came_before(
        self, fx_eraser: Processor, tmp_path: Path
    ) -> None:
        """Verify the eraser can be the first step: it works the frame out of the data of ``geometry.crop``.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        width, height = PAGE_PX
        facts: MetadataMap = {
            VersionData.FRAME: {**FRAME, 'left': 5.0, 'top': 7.0},
            VersionData.WIDTH_PX: width,
            VersionData.HEIGHT_PX: height,
        }
        output = run_step(fx_eraser, _page(tmp_path), tmp_path, facts=facts)
        assert output.image is not None
        erased = read_gray(output.image)
        expect(int(erased[0, 0]) == PAPER)
        expect(int(erased[height // 2, width // 2]) == TONE)
        assert_expectations()

    def test_a_page_with_neither_a_mask_nor_a_frame_is_left_as_it_is_and_marked_not_applied(
        self, fx_eraser: Processor, tmp_path: Path
    ) -> None:
        """Verify nothing is painted, the image is the input, and the page is marked for review.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = _page(tmp_path)
        output = run_step(fx_eraser, image, tmp_path)
        expect(output.image == image)
        expect(output.review is ReviewReason.NOT_APPLIED)
        expect(output.data[VersionData.SKIPPED] is True)
        assert_expectations()

    def test_the_fill_outside_the_frame_can_be_switched_off(self, fx_eraser: Processor, tmp_path: Path) -> None:
        """Verify a page with a frame and the parameter off is left as it is, since the user asked for no margin fill.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        output = run_step(
            fx_eraser, _page(tmp_path), tmp_path, {OUTSIDE: False}, facts={VersionData.CONTENT_FRAME: FRAME}
        )
        assert output.review is ReviewReason.NOT_APPLIED

    def test_a_black_and_white_page_stays_black_and_white_when_painted_round(
        self, fx_eraser: Processor, tmp_path: Path
    ) -> None:
        """Verify the mean colour of a bilevel page is black or white, not a gray.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = _page(tmp_path, tone=PAPER)
        output = run_step(fx_eraser, image, tmp_path, {FILL: EraserFill.AROUND, OUTSIDE: False}, mask=_mask(tmp_path))
        assert output.image is not None
        expect(set(np.unique(read_gray(output.image))) <= {INK, PAPER})
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_a_colour_page_is_painted_in_all_its_planes(self, fx_eraser: Processor, tmp_path: Path) -> None:
        """Verify black on a colour page makes the brushed pixels black in every plane.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        width, height = PAGE_PX
        page = np.full((height, width, 3), (200, 150, 100), dtype=np.uint8)
        image = save(Image.fromarray(page), tmp_path / PAGE_NAME)
        output = run_step(fx_eraser, image, tmp_path, {FILL: EraserFill.BLACK, OUTSIDE: False}, mask=_mask(tmp_path))
        assert output.image is not None
        erased = np.asarray(Image.open(output.image).convert('RGB'))
        expect(bool((erased[BRUSHED_ROWS, BRUSHED_COLUMNS] == INK).all()))
        expect(tuple(erased[0, 0]) == (200, 150, 100))
        expect(output.color_mode is ColorMode.COLOR)
        assert_expectations()

    def test_a_step_with_no_image_is_an_error(self, fx_eraser: Processor, tmp_path: Path) -> None:
        """Verify a step that reads an image fails with the key of the processor when it is given none.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        step_input = StepInput(image=None, params=fx_eraser.validate_params({}), workdir=tmp_path)
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_eraser.run(step_input)

    def test_a_mask_that_is_not_an_image_is_an_error(self, fx_eraser: Processor, tmp_path: Path) -> None:
        """Verify an unreadable mask file fails the step instead of passing as no mask.

        :param fx_eraser: The processor under test.
        :type fx_eraser: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        broken = tmp_path / MASK_NAME
        broken.write_bytes(b'not an image')
        with pytest.raises(ConflictError, match=MASK_NAME):
            run_step(fx_eraser, _page(tmp_path), tmp_path, mask=broken)
