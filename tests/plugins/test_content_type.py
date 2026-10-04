"""Tests for the pages.content processor, on pages of text and of pictures drawn for the tests.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import ColorMode, ContentType, ProcessorScope, Stage, VersionData
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.ports.processing import StepInput
from tests.plugins.cleanup_pages import lit_unevenly, run_step, text_ink, with_picture

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.ports.processing import Processor

PAGE_NAME: str = 'page.png'
KEY_PATTERN: str = r'pages\.content'
PAGE_SIZE_PX: tuple[int, int] = (600, 800)
# The paper of an old book, which has a yellow tint, as red, green and blue
YELLOWED_PAPER: tuple[int, int, int] = (230, 208, 164)
WHITE_PAPER: tuple[int, int, int] = (255, 255, 255)
# Where the picture of a plate lies, as rows then columns, which is most of the page
PLATE_ROWS: slice = slice(60, 740)
PLATE_COLUMNS: slice = slice(50, 550)
# The tones of the picture: where they lie, how far they swing, and the noise of the sensor
PLATE_MEAN: float = 120.0
PLATE_SWING: float = 80.0
PLATE_NOISE: float = 10.0
LIGHTEST_TONE: float = 235.0
DARKEST_TONE: float = 20.0
TONE_RANGE: float = 255.0
# How the colour picture differs from the paper: the share of red follows a slow wave, and green and blue differ
COLOR_WAVE_PX: float = 40.0
RED_SHARE: float = 0.5
GREEN_SHARE: float = 0.8
BLUE_SHARE: float = 0.4


def plate(paper: tuple[int, int, int], *, colour: bool) -> NDArray[np.uint8]:
    """Draw a page that a picture of continuous tones covers most of, on paper of a tint.

    A picture in black and white is the paper darkened by the tone of each pixel, so every pixel has the tint of the
    paper, as a print on yellowed paper has. A picture in colour has a colour of its own at each pixel.

    :param paper: Colour of the paper as red, green and blue.
    :type paper: tuple[int, int, int]
    :param colour: Whether the picture is in colour.
    :type colour: bool
    :returns: The samples of the page, with three planes in the order red, green and blue.
    :rtype: NDArray[np.uint8]
    """
    width, height = PAGE_SIZE_PX
    rows, columns = np.mgrid[0 : PLATE_ROWS.stop - PLATE_ROWS.start, 0 : PLATE_COLUMNS.stop - PLATE_COLUMNS.start]
    tone = PLATE_MEAN + PLATE_SWING * np.sin(columns / 25.0) * np.cos(rows / 18.0)
    tone += np.random.default_rng(1).normal(0, PLATE_NOISE, tone.shape)
    darkness = np.clip(tone, DARKEST_TONE, LIGHTEST_TONE) / TONE_RANGE
    page = np.empty((height, width, 3), dtype=np.float64)
    page[:] = paper
    if colour:
        wave = 0.5 + 0.5 * np.sin(columns / COLOR_WAVE_PX)
        planes = (wave * RED_SHARE, np.full_like(wave, GREEN_SHARE), np.full_like(wave, BLUE_SHARE))
        page[PLATE_ROWS, PLATE_COLUMNS] = np.stack(planes, axis=-1) * darkness[..., np.newaxis] * TONE_RANGE
    else:
        page[PLATE_ROWS, PLATE_COLUMNS] = np.asarray(paper)[np.newaxis, np.newaxis, :] * darkness[..., np.newaxis]
    return np.asarray(np.clip(page, 0, TONE_RANGE), dtype=np.uint8)


def detect(processor: Processor, samples: NDArray[np.uint8], folder: Path) -> tuple[ContentType, float]:
    """Run the processor on a page drawn for the test.

    :param processor: The processor under test.
    :type processor: Processor
    :param samples: Gray samples, or red, green and blue.
    :type samples: NDArray[np.uint8]
    :param folder: Directory the page is written to and the step writes into.
    :type folder: Path
    :returns: What the processor found the page to show, and the share of the page the pictures cover.
    :rtype: tuple[ContentType, float]
    """
    path = folder / PAGE_NAME
    Image.fromarray(samples).save(path)
    output = run_step(processor, path, folder)
    return ContentType(output.data[VersionData.CONTENT_TYPE]), float(output.data[VersionData.PICTURE_SHARE])


class TestContentTypeProbe:
    """Tests for ContentTypeProbe."""

    def test_spec_is_a_step_of_the_page_order_that_writes_no_image(self, fx_content_type: Processor) -> None:
        """Verify the spec: the key, the stage no recipe is made of, the page scope and no output.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        """
        spec = fx_content_type.spec
        expect(spec.key == 'pages.content')
        expect(spec.stage is Stage.PAGE_ORDER)
        expect(spec.scope is ProcessorScope.PAGE)
        expect(spec.outputs == frozenset())
        assert_expectations()

    def test_the_processor_takes_no_parameters(self, fx_content_type: Processor) -> None:
        """Verify an empty set of parameters is valid, and a misspelt one is an error.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        """
        expect(fx_content_type.validate_params({}) == {})
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_content_type.validate_params({'share': 0.5})
        assert_expectations()

    def test_a_page_of_text_is_text(self, fx_content_type: Processor, tmp_path: Path) -> None:
        """Verify lines of words, lit unevenly as a scan is, cover no share of the page with pictures.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        content, share = detect(fx_content_type, lit_unevenly(text_ink()), tmp_path)
        expect(content is ContentType.TEXT)
        expect(share == pytest.approx(0.0, abs=0.02))
        assert_expectations()

    def test_a_page_of_text_with_a_small_picture_is_still_text(
        self, fx_content_type: Processor, tmp_path: Path
    ) -> None:
        """Verify a picture under a few lines of text, which covers a sixth of the page, does not make a plate of it.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        content, share = detect(fx_content_type, with_picture(lit_unevenly(text_ink())), tmp_path)
        expect(content is ContentType.TEXT)
        expect(0.05 < share < 0.4)
        assert_expectations()

    def test_a_gray_page_that_a_picture_covers_is_a_picture_in_black_and_white(
        self, fx_content_type: Processor, tmp_path: Path
    ) -> None:
        """Verify a page of one plane has no colour for the picture to have.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        gray = np.asarray(Image.fromarray(plate(WHITE_PAPER, colour=False)).convert('L'), dtype=np.uint8)

        content, share = detect(fx_content_type, gray, tmp_path)

        expect(content is ContentType.BW_PICTURE)
        expect(share > 0.5)
        assert_expectations()

    def test_a_picture_printed_on_yellowed_paper_is_in_black_and_white(
        self, fx_content_type: Processor, tmp_path: Path
    ) -> None:
        """Verify the tint of the paper in a colour scan does not make the engraving printed on it a colour picture.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        content, _ = detect(fx_content_type, plate(YELLOWED_PAPER, colour=False), tmp_path)

        assert content is ContentType.BW_PICTURE

    def test_a_picture_with_colours_of_its_own_is_in_colour(self, fx_content_type: Processor, tmp_path: Path) -> None:
        """Verify a picture whose pixels differ in hue from the paper is a colour picture.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        content, _ = detect(fx_content_type, plate(YELLOWED_PAPER, colour=True), tmp_path)

        assert content is ContentType.COLOR_PICTURE

    def test_the_colour_mode_of_the_output_follows_the_planes_of_the_image(
        self, fx_content_type: Processor, tmp_path: Path
    ) -> None:
        """Verify the output says gray for one plane and colour for three, and writes no image.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / PAGE_NAME
        Image.fromarray(lit_unevenly(text_ink())).save(path)
        gray = run_step(fx_content_type, path, tmp_path)
        Image.fromarray(plate(YELLOWED_PAPER, colour=False)).save(path)
        coloured = run_step(fx_content_type, path, tmp_path)

        expect((gray.color_mode, gray.image) == (ColorMode.GRAY, None))
        expect((coloured.color_mode, coloured.image) == (ColorMode.COLOR, None))
        assert_expectations()

    def test_a_step_without_an_image_is_an_error(self, fx_content_type: Processor, tmp_path: Path) -> None:
        """Verify the processor refuses to run when it has no image to read.

        :param fx_content_type: The processor under test.
        :type fx_content_type: Processor
        :param tmp_path: Directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_content_type.run(StepInput(image=None, params={}, workdir=tmp_path))
