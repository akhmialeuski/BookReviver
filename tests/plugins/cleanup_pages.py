"""Pages drawn for the tests of the Cleanup steps, with the ground truth of their ink, and a runner of one step.

The text is drawn as lines of letters made of stems and bars, so the ground truth is exact: the pixels that were drawn
are the ink. A page of uneven light darkens the paper from one corner to the opposite one, until the paper there is as
dark as the ink is in the other corner, so no single threshold parts them.
"""

import random
from typing import TYPE_CHECKING, NotRequired, TypedDict, Unpack
from uuid import uuid4

import numpy as np
from PIL import Image

from bookreviver.domain.ids import PageId
from bookreviver.ports.processing import StepInput
from tests.helpers.builders import make_geometry_edit

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor, StepOutput

PAGE_SIZE_PX: tuple[int, int] = (600, 800)
LINE_PITCH_PX: int = 40
LETTER_HEIGHT_PX: int = 24
STEM_WIDTH_PX: int = 4
LETTER_WIDTH_RANGE_PX: tuple[int, int] = (10, 18)
LETTER_GAP_PX: int = 4
WORD_GAP_PX: int = 14
LETTERS_PER_WORD: tuple[int, int] = (3, 8)
# The chance a letter has a bar across its top or its middle, and the row of the middle one
BAR_CHANCE: float = 0.5
MIDDLE_BAR_ROW_PX: int = 10
LEFT_MARGIN_PX: int = 50
TOP_MARGIN_PX: int = 60
# The tone of the paper at the lit corner and how far it falls to the dark one, and the share of the paper's tone the ink has
LIT_PAPER: float = 240.0
LIGHT_FALL: float = 165.0
INK_SHARE: float = 0.12
NOISE_SPREAD: float = 3.0
INK: int = 0
PAPER: int = 255
# Where the picture of a mixed page lies, as rows then columns, and the tones it is drawn in
PICTURE_ROWS: slice = slice(500, 720)
PICTURE_COLUMNS: slice = slice(120, 480)
PICTURE_MEAN: float = 120.0
PICTURE_SWING: float = 70.0
PICTURE_NOISE: float = 10.0
# The line of letters with marks: its size, the stem, and the dot that stands over it
MARKS_PAGE_PX: tuple[int, int] = (600, 800)
MARK_LETTER_PITCH_PX: int = 30
MARK_STEM_PX: tuple[int, int] = (4, 40)
MARK_DOT_PX: int = 5
MARK_DOT_GAP_PX: int = 5


class StepExtras(TypedDict):
    """What a test may give a step besides its image and its parameters.

    :ivar edit: The shape of the manual edit of the page.
    :ivar mask: Path of the mask of a brush edit.
    :ivar facts: Data of the input version.
    :ivar scale: Ratio of the image to the full image, 1 for a full run.
    """

    edit: NotRequired[EditGeometry]
    mask: NotRequired[Path]
    facts: NotRequired[MetadataMap]
    scale: NotRequired[float]


def run_step(
    processor: Processor, image: Path, workdir: Path, params: MetadataMap | None = None, **extras: Unpack[StepExtras]
) -> StepOutput:
    """Run the step on an image and return its one output.

    :param processor: The processor under test.
    :type processor: Processor
    :param image: Image to read.
    :type image: Path
    :param workdir: Directory the step writes into.
    :type workdir: Path
    :param params: Parameters of the step, the defaults where none are given.
    :type params: MetadataMap | None
    :param extras: The edit, the mask, the facts of the input and the scale, each when the test gives it.
    :type extras: Unpack[StepExtras]
    :returns: The output.
    :rtype: StepOutput
    """
    shape = extras.get('edit')
    edit = None if shape is None else make_geometry_edit(page_id=PageId(uuid4()), geometry=shape)
    step_input = StepInput(
        image=image,
        params=processor.validate_params(params or {}),
        edit=edit,
        edit_mask=extras.get('mask'),
        scale=extras.get('scale', 1.0),
        input_data=extras.get('facts', {}),
        workdir=workdir,
    )
    [output] = processor.run(step_input).outputs
    return output


def read_gray(path: Path) -> NDArray[np.uint8]:
    """Read an image as one plane of samples.

    :param path: Path of the image.
    :type path: Path
    :returns: The samples.
    :rtype: NDArray[np.uint8]
    """
    return np.asarray(Image.open(path).convert('L'), dtype=np.uint8)


def text_ink(*, seed: int = 3) -> NDArray[np.bool_]:
    """Draw the ground truth of a page of text: lines of words made of stems and bars.

    :param seed: Seed of the letters, so a page is the same every time.
    :type seed: int
    :returns: True where a pixel is ink.
    :rtype: NDArray[np.bool_]
    """
    width, height = PAGE_SIZE_PX
    chance = random.Random(seed)
    ink = np.zeros((height, width), dtype=np.bool_)
    for top in range(TOP_MARGIN_PX, height - TOP_MARGIN_PX, LINE_PITCH_PX):
        left = LEFT_MARGIN_PX
        while left < width - 2 * LEFT_MARGIN_PX:
            for _ in range(chance.randint(*LETTERS_PER_WORD)):
                letter = chance.randint(*LETTER_WIDTH_RANGE_PX)
                ink[top : top + LETTER_HEIGHT_PX, left : left + STEM_WIDTH_PX] = True
                if chance.random() < BAR_CHANCE:
                    ink[top + MIDDLE_BAR_ROW_PX : top + MIDDLE_BAR_ROW_PX + STEM_WIDTH_PX, left : left + letter] = True
                if chance.random() < BAR_CHANCE:
                    ink[top : top + STEM_WIDTH_PX, left : left + letter] = True
                left += letter + LETTER_GAP_PX
            left += WORD_GAP_PX
    return ink


def lit_unevenly(ink: NDArray[np.bool_], *, seed: int = 3) -> NDArray[np.uint8]:
    """Light a page of ink from one corner, so the paper at the far corner is as dark as the ink at the near one.

    :param ink: True where a pixel is ink.
    :type ink: NDArray[np.bool_]
    :param seed: Seed of the noise of the sensor.
    :type seed: int
    :returns: The gray samples of the scan.
    :rtype: NDArray[np.uint8]
    """
    height, width = ink.shape
    rows, columns = np.mgrid[0:height, 0:width]
    light = LIT_PAPER - LIGHT_FALL * (columns / width * 0.6 + rows / height * 0.4)
    tone = np.where(ink, light * INK_SHARE, light)
    tone += np.random.default_rng(seed).normal(0, NOISE_SPREAD, tone.shape)
    return np.asarray(np.clip(tone, INK, PAPER), dtype=np.uint8)


def with_picture(scan: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Paint a picture of continuous tones over the lower part of a scan.

    :param scan: The gray samples of a page of text.
    :type scan: NDArray[np.uint8]
    :returns: A copy with the picture.
    :rtype: NDArray[np.uint8]
    """
    page = scan.copy()
    rows, columns = np.mgrid[
        0 : PICTURE_ROWS.stop - PICTURE_ROWS.start, 0 : PICTURE_COLUMNS.stop - PICTURE_COLUMNS.start
    ]
    tones = PICTURE_MEAN + PICTURE_SWING * np.sin(columns / 25.0) * np.cos(rows / 18.0)
    tones += np.random.default_rng(1).normal(0, PICTURE_NOISE, tones.shape)
    page[PICTURE_ROWS, PICTURE_COLUMNS] = np.clip(tones, INK, PAPER).astype(np.uint8)
    return page


def marks_line() -> tuple[NDArray[np.uint8], list[tuple[int, int]]]:
    """Draw a line of stems that carry a dot each, as the letters і and ё do.

    :returns: The black and white page, and the centres of the dots in rows and columns.
    :rtype: tuple[NDArray[np.uint8], list[tuple[int, int]]]
    """
    width, height = MARKS_PAGE_PX
    page = np.full((height, width), PAPER, dtype=np.uint8)
    stem_width, stem_height = MARK_STEM_PX
    top = height // 2
    dots: list[tuple[int, int]] = []
    for left in range(LEFT_MARGIN_PX, width - LEFT_MARGIN_PX, MARK_LETTER_PITCH_PX):
        page[top : top + stem_height, left : left + stem_width] = INK
        dot_top = top - MARK_DOT_GAP_PX - MARK_DOT_PX
        page[dot_top : dot_top + MARK_DOT_PX, left : left + MARK_DOT_PX] = INK
        dots.append((dot_top + MARK_DOT_PX // 2, left + MARK_DOT_PX // 2))
    return page, dots
