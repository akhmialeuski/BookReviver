"""A blank leaf: a page of a given size drawn in white or in the colour of the paper of the book.

``pages.blank`` draws the page of the size and the resolution in its parameters with libvips. A white page is a 1-bit
PNG, because it has two colours at most and so is a bilevel page whatever the project's image policy. ``Image.black``
makes a black image of unsigned characters, so adding 255 makes it white and keeps the single band. The resolution is
written in pixels per millimetre. libvips always writes a resolution, so a leaf made without one carries its default of
one pixel per millimetre, and the resolution the book keeps is the one in the data of the version, which says it is
unknown.

With the fill ``paper`` the leaf takes the colour of the paper of the neighbouring pages, whose images the step is given
as its references. The paper of a page is the median colour of the pixels on the bright side of the split of Otsu, as
``cv_image.paper_colour`` finds it for the steps of the geometry, and the leaf takes the median of the pages. It is
measured here on a thumbnail with libvips, since a leaf is made on a machine without the OpenCV extra too. A page with
no paper to part from its ink, such as an empty scan, says nothing, and a leaf with no paper to take is white. A leaf
whose paper is gray is a gray page, and one whose paper is white is the white bilevel page.
"""

import statistics
from collections import Counter
from typing import TYPE_CHECKING, override

import pyvips
from pydantic import Field

from bookreviver.domain.enums import ColorMode, PaperFill, ProcessorScope, Stage, VersionOutput
from bookreviver.domain.values import COLOR_MODE_KEY, PageSize, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.ports.processing import StepInput

MM_PER_INCH: float = 25.4
WHITE: int = 255
# The depth of a pixel of a bilevel PNG in bits
BILEVEL_BIT_DEPTH: int = 1
BLANK_IMAGE_NAME: str = 'blank.png'
# The longer side of the thumbnail the paper of a page is measured on, and the planes of a colour image
PAPER_SAMPLE_PX: int = 128
COLOR_PLANES: int = 3
# Difference of the mean tones of the two classes of the split, below which the page has no paper to part from its ink
MIN_TONE_CONTRAST: float = 40.0
# Weights of the red, green and blue samples in the tone of a pixel, in thousandths
RED_WEIGHT: int = 299
GREEN_WEIGHT: int = 587
BLUE_WEIGHT: int = 114
WEIGHT_SCALE: int = 1000
MAX_PAPER_PAGES: int = 64


class BlankParams(Params):
    """The size and the fill of the leaf.

    :ivar width_px: Width of the leaf in pixels.
    :ivar height_px: Height of the leaf in pixels.
    :ivar dpi: Resolution of the leaf in dots per inch, or None when it is unknown.
    :ivar fill: Whether the leaf is white or has the colour of the paper of the pages it is given.
    :ivar paper_from: Versions of the neighbouring pages whose paper the leaf takes. The step reads their images from
                      its references, and the identifiers are what makes a leaf made from other pages another version.
    """

    width_px: int = Field(gt=0, title='Width', description='Width of the leaf in pixels')
    height_px: int = Field(gt=0, title='Height', description='Height of the leaf in pixels')
    dpi: float | None = Field(
        default=None, gt=0, title='Resolution', description='Resolution of the leaf in dots per inch, if known'
    )
    fill: PaperFill = Field(
        default=PaperFill.WHITE,
        title='Fill',
        description='white draws a white leaf, paper the colour of the paper of the neighbouring pages',
    )
    paper_from: list[str] = Field(
        default_factory=list,
        max_length=MAX_PAPER_PAGES,
        title='Pages to take the paper from',
        description='Versions of the neighbouring pages whose paper the leaf takes, for the fill paper',
    )


class BlankPage(ModelProcessor):
    """Draws a white page of a given size."""

    params_model = BlankParams
    spec = ProcessorSpec(
        key='pages.blank',
        version='1',
        title='Blank leaf',
        stage=Stage.PAGE_ORDER,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=BlankParams.model_json_schema(),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Write the page into the work directory.

        :param step_input: The step, whose parameters give the size and the fill of the leaf, and whose references are
                           the images of the pages the paper is taken from.
        :type step_input: StepInput
        :returns: One output holding the leaf, with its size in the data, which is bilevel when the leaf is white.
        :rtype: StepResult
        """
        params = BlankParams.model_validate(step_input.params)
        paper = median_paper(step_input.references) if params.fill is PaperFill.PAPER else None
        if paper is None or set(paper) == {WHITE}:
            leaf = (pyvips.Image.black(params.width_px, params.height_px) + WHITE).cast(pyvips.enums.BandFormat.UCHAR)
            color_mode = ColorMode.BILEVEL
        else:
            leaf = (pyvips.Image.black(params.width_px, params.height_px, bands=len(paper)) + list(paper)).cast(
                pyvips.enums.BandFormat.UCHAR
            )
            color_mode = ColorMode.COLOR if len(paper) == COLOR_PLANES else ColorMode.GRAY
        if params.dpi is not None:
            per_mm = params.dpi / MM_PER_INCH
            leaf = leaf.copy(xres=per_mm, yres=per_mm)
        target = step_input.workdir / BLANK_IMAGE_NAME
        if color_mode is ColorMode.BILEVEL:
            leaf.pngsave(str(target), bitdepth=BILEVEL_BIT_DEPTH)
        else:
            leaf.pngsave(str(target))
        size = PageSize(width_px=params.width_px, height_px=params.height_px, dpi=params.dpi)
        data = (
            size.as_data() if color_mode is ColorMode.BILEVEL else {**size.as_data(), COLOR_MODE_KEY: color_mode.value}
        )
        return StepResult(outputs=[StepOutput(image=target, color_mode=color_mode, data=data)])


def median_paper(images: Sequence[Path]) -> tuple[int, ...] | None:
    """Give the colour the paper of several pages has in the median.

    :param images: Local paths of the images of the pages.
    :type images: Sequence[Path]
    :returns: One tone for a gray paper and the red, green and blue for a coloured one, or None when no page has paper
              to measure.
    :rtype: tuple[int, ...] | None
    """
    colours = [colour for image in images if (colour := paper_of(image)) is not None]
    if not colours:
        return None
    medians = tuple(round(statistics.median(colour[plane] for colour in colours)) for plane in range(COLOR_PLANES))
    return medians[:1] if len(set(medians)) == 1 else medians


def otsu_threshold(tones: Sequence[int]) -> int:
    """Find the tone that parts the pixels of an image into the dark class and the bright class of Otsu.

    The threshold is the tone that leaves the largest variance between the two classes, which parts the ink of a page
    from its paper.

    :param tones: The tone of every pixel, from 0 to 255.
    :type tones: Sequence[int]
    :returns: The brightest tone of the dark class.
    :rtype: int
    """
    counts = Counter(tones)
    total, tone_sum = len(tones), sum(tones)
    dark_count = dark_sum = 0
    best, threshold = -1.0, 0
    for tone in range(WHITE + 1):
        dark_count += counts[tone]
        dark_sum += tone * counts[tone]
        if dark_count in {0, total}:
            continue
        bright_count = total - dark_count
        gap = dark_sum / dark_count - (tone_sum - dark_sum) / bright_count
        if (spread := dark_count * bright_count * gap**2) > best:
            best, threshold = spread, tone
    return threshold


def paper_of(image: Path) -> tuple[int, int, int] | None:
    """Measure the paper of one page: the median colour of the pixels on the bright side of the split of Otsu.

    :param image: Local path of the image of the page.
    :type image: Path
    :returns: The red, green and blue of the paper, or None when the page has no paper to part from its ink.
    :rtype: tuple[int, int, int] | None
    """
    sample = pyvips.Image.thumbnail(str(image), PAPER_SAMPLE_PX)
    if sample.hasalpha():
        sample = sample.flatten(background=[WHITE])
    sample = sample.colourspace(pyvips.enums.Interpretation.SRGB).cast(pyvips.enums.BandFormat.UCHAR)
    # libvips hands out a buffer that cannot be sliced with a step, so it is copied to bytes first
    pixels = bytes(sample.write_to_memory())
    reds, greens, blues = pixels[0::COLOR_PLANES], pixels[1::COLOR_PLANES], pixels[2::COLOR_PLANES]
    tones = [
        (RED_WEIGHT * red + GREEN_WEIGHT * green + BLUE_WEIGHT * blue) // WEIGHT_SCALE
        for red, green, blue in zip(reds, greens, blues, strict=True)
    ]
    threshold = otsu_threshold(tones)
    bright = [index for index, tone in enumerate(tones) if tone > threshold]
    dark = len(tones) - len(bright)
    if not bright or dark == 0:
        return None
    bright_sum = sum(tones[index] for index in bright)
    if bright_sum / len(bright) - (sum(tones) - bright_sum) / dark < MIN_TONE_CONTRAST:
        return None
    red, green, blue = (round(statistics.median(plane[index] for index in bright)) for plane in (reds, greens, blues))
    return red, green, blue
