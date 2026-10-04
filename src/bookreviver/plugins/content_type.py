"""What a page shows: text, or a picture in colour or in black and white, which ``pages.content`` proposes for a page.

The processor reads the preview of the base image of a page and writes no image. It finds the pictures the way the
step ``cleanup.binarize`` does for its mixed mode: the light of the paper is levelled out, and a neighbourhood that is
made of continuous tones, which text never is, belongs to a picture. The share of the page that the pictures cover
decides between text and a picture, and a page with a few small ornaments or a single woodcut under a paragraph of text
stays text, since the share is far below the limit.

The colour of a picture is told from its chromaticity, the share of red and green in a pixel, since the tone of a pixel
changes with its darkness and its chromaticity does not. The paper of an old book has a yellow tint, and the ink and the
grays of an engraving printed on it have the chromaticity of that paper. A picture is in colour when a share of its
pixels has a chromaticity that differs from the paper's, and it is black and white when its pixels all have the tint of
the paper.

The processor belongs to the page order, which no recipe is made of, and runs only through the job that detects the
content of the pages, so it is never offered as a step.
"""

from typing import TYPE_CHECKING, override

import cv2
import numpy as np

from bookreviver.domain.enums import ColorMode, ContentType, ProcessorScope, Stage, VersionData
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.values import COLOR_MODE_KEY, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.cv_image import COLOR_PLANES, OtsuSplit, read_samples
from bookreviver.plugins.pictures import PictureZones, level_light
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Floats, Samples
    from bookreviver.ports.processing import StepInput

NO_IMAGE: str = 'The step {key} needs the image of its input.'
# A page is a picture when pictures cover at least this share of it. A page of text with a woodcut under a paragraph
# stays text, and a plate with a caption is a picture
PICTURE_PAGE_SHARE: float = 0.4
# The darkest pixel whose chromaticity is read, as the sum of its three samples: the share of a dark pixel is noise
MIN_SAMPLE_SUM: int = 3 * 40
# How far the chromaticity of a pixel lies from the paper's, in shares of red and green, for the pixel to be coloured
CHROMA_DEVIATION: float = 0.05
# The share of the pixels of the pictures that are coloured, from which the pictures are in colour
COLOR_PIXEL_SHARE: float = 0.1
# The chromaticity of a gray: a third of each sample, which the paper is taken to have when the page shows none of it
NEUTRAL_SHARE: float = 1 / 3


class ContentTypeParams(Params):
    """The processor takes no parameters."""


def picture_mask(page: Samples) -> Samples:
    """Paint the pictures of a page.

    :param page: Gray or colour samples of the page.
    :type page: Samples
    :returns: A plane the size of the page that is 1 in a picture and 0 elsewhere.
    :rtype: Samples
    """
    gray = np.asarray(cv2.cvtColor(page, cv2.COLOR_BGR2GRAY) if page.ndim == COLOR_PLANES else page, dtype=np.uint8)
    search = PictureZones(level_light(gray))
    return search.mask(search.find(), None)


def chromaticity(pixels: Floats) -> Floats:
    """Give the shares of red and green of the pixels that are bright enough to have a steady chromaticity.

    :param pixels: Blue, green and red samples of the pixels, one row for each.
    :type pixels: Floats
    :returns: One row of the share of red and the share of green for each pixel that is bright enough.
    :rtype: Floats
    """
    bright = pixels[pixels.sum(axis=1) >= MIN_SAMPLE_SUM]
    shares = np.column_stack((bright[:, 2], bright[:, 1])) / bright.sum(axis=1, keepdims=True)
    return np.asarray(shares, dtype=np.float64)


def has_color(page: Samples, mask: Samples) -> bool:
    """Tell whether the pictures of a page are in colour, which the tint of its paper does not make them.

    :param page: Samples of the page, with three planes for a colour page.
    :type page: Samples
    :param mask: The pictures of the page, 1 in a picture and 0 elsewhere.
    :type mask: Samples
    :returns: True when enough pixels of the pictures have a chromaticity other than the paper's, False for a page of a
              single plane.
    :rtype: bool
    """
    if page.ndim != COLOR_PLANES:
        return False
    gray = np.asarray(cv2.cvtColor(page, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
    in_picture = mask.astype(np.bool_)
    paper = chromaticity(page[(gray > OtsuSplit.of(gray).threshold) & ~in_picture].astype(np.float64))
    pictured = chromaticity(page[in_picture].astype(np.float64))
    if not pictured.size:
        return False
    tint = np.median(paper, axis=0) if paper.size else np.full(2, NEUTRAL_SHARE)
    coloured = np.linalg.norm(pictured - tint, axis=1) > CHROMA_DEVIATION
    return bool(coloured.mean() >= COLOR_PIXEL_SHARE)


class ContentTypeProbe(ModelProcessor):
    """Proposes what a page shows, from the share of it that pictures cover and from the colour of the pictures."""

    params_model = ContentTypeParams
    spec = ProcessorSpec(
        key='pages.content',
        version='1',
        title='Content type',
        stage=Stage.PAGE_ORDER,
        scope=ProcessorScope.PAGE,
        outputs=frozenset(),
        parameters=ContentTypeParams.model_json_schema(),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find the pictures of the page and tell what it shows.

        :param step_input: The preview of the base image of the page.
        :type step_input: StepInput
        :returns: One output without an image, whose data hold the content type and the share the pictures cover.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        page = read_samples(step_input.image)
        mask = picture_mask(page)
        share = float(mask.astype(np.bool_).mean())
        if share < PICTURE_PAGE_SHARE:
            content = ContentType.TEXT
        elif has_color(page, mask):
            content = ContentType.COLOR_PICTURE
        else:
            content = ContentType.BW_PICTURE
        color_mode = ColorMode.COLOR if page.ndim == COLOR_PLANES else ColorMode.GRAY
        return StepResult(
            outputs=[
                StepOutput(
                    color_mode=color_mode,
                    data={
                        VersionData.CONTENT_TYPE: content.value,
                        VersionData.PICTURE_SHARE: share,
                        COLOR_MODE_KEY: color_mode.value,
                    },
                )
            ]
        )
