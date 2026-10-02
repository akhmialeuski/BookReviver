"""Reading and writing the images of the OpenCV plugins.

A plugin that needs OpenCV reads its input as an array of 8-bit samples, a plane for a gray or a bilevel page and three
planes, blue, green and red, for a colour one, and writes what it made as a PNG, which loses nothing. The service picks
the format a version is stored in later. A scan in a high bit depth is brought down to eight bits, and a transparent
plane is dropped, since a page has no background to show through.

The colour mode comes from the facts of the input when they say it, and from the samples when they do not: a page whose
samples are all black or all white is bilevel. A bilevel page that a plugin resamples is made bilevel again, so a
rotated black-and-white page does not become a gray one.
"""

from typing import TYPE_CHECKING

import cv2
import numpy as np
from attrs import frozen

from bookreviver.domain.enums import ColorMode, ReviewReason, VersionData
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.values import COLOR_MODE_KEY, PageSize

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.domain.values import MetadataMap

type Samples = NDArray[np.uint8]
type Floats = NDArray[np.float64]
type Indices = NDArray[np.int_]

WHITE: int = 255
BLACK: int = 0
# The depth in bits a 16-bit sample is brought down from, as the ratio of the two ranges
SAMPLE_16_TO_8: float = 1 / 257
# A sample at or above this is white once a bilevel page is made bilevel again
BILEVEL_THRESHOLD: int = 128
# The planes of a colour image that carry colour, without a transparent plane
COLOR_PLANES: int = 3
UNREADABLE_IMAGE: str = 'The image {path} cannot be read.'
UNWRITABLE_IMAGE: str = 'The image {path} cannot be written.'
NO_IMAGE: str = 'The step {key} needs the image of its input.'
# The confidence of what the user gave by hand, such as an angle or a cut line, which no search is sure of better
MANUAL_CONFIDENCE: float = 1.0
# Difference of the mean samples of the two classes of a split, below which the split means nothing, as on a page with
# no paper to part from its background or no ink to part from its paper
MIN_TONE_CONTRAST: float = 40.0
# What a step hands on to the steps after it besides the size and the colour of its image: the sides of the sheet cut by
# the scan, and the reason an earlier step wants the page looked at, so that the last step of a recipe, whose version is
# the one the stage stands on, carries the reasons of all of them
CARRIED_DATA: tuple[VersionData, ...] = (VersionData.CUT_EDGES, VersionData.REVIEW)


@frozen(kw_only=True)
class OtsuSplit:
    """How a gray image falls into a bright class and a dark class by the threshold of Otsu.

    :ivar threshold: Sample value above which a pixel is in the bright class.
    :ivar contrast: Difference of the mean samples of the two classes, 0 when one class is empty.
    :ivar separability: Share of the variance of the samples that lies between the classes, from 0 for an image that
                        is all one tone to 1 for one of two tones.
    """

    threshold: float
    contrast: float
    separability: float

    @classmethod
    def of(cls, gray: Samples) -> OtsuSplit:
        """Split an image by the threshold of Otsu and measure how far apart the two classes are.

        :param gray: Single-plane samples.
        :type gray: Samples
        :returns: The threshold and the measures of the split.
        :rtype: OtsuSplit
        """
        threshold, _ = cv2.threshold(gray, 0, WHITE, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        bright = gray > threshold
        if not bright.any() or bright.all():
            return cls(threshold=float(threshold), contrast=0.0, separability=0.0)
        share = float(bright.mean())
        contrast = float(gray[bright].mean()) - float(gray[~bright].mean())
        total = float(gray.var())
        between = share * (1 - share) * contrast**2
        return cls(threshold=float(threshold), contrast=contrast, separability=between / total if total > 0 else 0.0)


def odd_size(size: float) -> int:
    """Round a size to the nearest odd number of pixels, at least 1.

    A kernel of an odd size has its anchor in the middle, which a closing needs to give back the edges of what it fills
    instead of shifting them.

    :param size: Size in pixels.
    :type size: float
    :returns: The odd size.
    :rtype: int
    """
    return max(1, round(size) // 2 * 2 + 1)


def read_samples(path: Path) -> Samples:
    """Read an image as 8-bit samples, gray or blue, green and red.

    :param path: Local path of the image file.
    :type path: Path
    :returns: The samples, with two dimensions for a gray image and three for a colour one.
    :rtype: Samples
    :raises ConflictError: If the file is no image OpenCV reads.
    """
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ConflictError(UNREADABLE_IMAGE.format(path=path.name))
    if image.dtype != np.uint8:
        image = cv2.convertScaleAbs(image, alpha=SAMPLE_16_TO_8)
    if image.ndim == COLOR_PLANES and image.shape[2] > COLOR_PLANES:
        image = image[:, :, :COLOR_PLANES]
    return np.asarray(image, dtype=np.uint8)


def color_mode_of(image: Samples, facts: MetadataMap) -> ColorMode:
    """Tell the colour mode of an image, from what the input says of itself or else from its samples.

    :param image: The samples of the image.
    :type image: Samples
    :param facts: Data of the input version, or the facts of the scan, which may name the colour mode.
    :type facts: MetadataMap
    :returns: The colour mode.
    :rtype: ColorMode
    """
    if image.ndim == COLOR_PLANES:
        return ColorMode.COLOR
    stated = facts.get(COLOR_MODE_KEY)
    if stated in {ColorMode.BILEVEL, ColorMode.GRAY}:
        return ColorMode(stated)
    return ColorMode.BILEVEL if np.all((image == BLACK) | (image == WHITE)) else ColorMode.GRAY


def to_bilevel(image: Samples) -> Samples:
    """Make a resampled bilevel page black and white again.

    :param image: Gray samples with the grays resampling made at the edges of the ink.
    :type image: Samples
    :returns: The samples, every one black or white.
    :rtype: Samples
    """
    return np.where(image >= BILEVEL_THRESHOLD, WHITE, BLACK).astype(np.uint8)


def write_png(image: Samples, path: Path) -> None:
    """Write samples as a PNG.

    :param image: The samples to write.
    :type image: Samples
    :param path: Local path of the file to make.
    :type path: Path
    :raises ConflictError: If OpenCV cannot write the file.
    """
    if not cv2.imwrite(str(path), image):
        raise ConflictError(UNWRITABLE_IMAGE.format(path=path.name))


def image_data(image: Samples, facts: MetadataMap, color_mode: ColorMode) -> dict[str, object]:
    """Describe an image the way a version records it: its size, its resolution and its colour mode.

    The resolution is the one of the input, since a step that resamples without scaling keeps it.

    :param image: The samples of the image.
    :type image: Samples
    :param facts: Data of the input version, or the facts of the scan, which may hold the resolution.
    :type facts: MetadataMap
    :param color_mode: Colour mode of the image.
    :type color_mode: ColorMode
    :returns: The size, the resolution if it is known, and the colour mode.
    :rtype: dict[str, object]
    """
    height, width = image.shape[:2]
    dpi = facts.get(VersionData.DPI)
    size = PageSize(width_px=width, height_px=height, dpi=dpi if isinstance(dpi, int | float) and dpi > 0 else None)
    data: dict[str, object] = {**size.as_data(), COLOR_MODE_KEY: color_mode.value}
    data.update({key.value: facts[key] for key in CARRIED_DATA if key in facts})
    return data


def source_size_data(image: Samples, scale: float) -> dict[str, object]:
    """Give the size in pixels of the full image a step read, which the editor of the step draws its shape on.

    A step that ran on a preview read an image smaller than the full one by the ratio ``scale``, and the shapes it
    reports are in the pixels of the full image, so the size is given in them too.

    :param image: The samples the step read.
    :type image: Samples
    :param scale: Size of the image over the size of the full image, 1 for a full run.
    :type scale: float
    :returns: The width and the height of the full image.
    :rtype: dict[str, object]
    """
    height, width = image.shape[:2]
    return {VersionData.SOURCE_WIDTH_PX: round(width / scale), VersionData.SOURCE_HEIGHT_PX: round(height / scale)}


def settle_review(data: dict[str, object], own: ReviewReason | None, facts: MetadataMap) -> ReviewReason | None:
    """Choose the reason a step marks its page for review, and record it in the data for the steps after it.

    The reason of the step itself wins, since it is the more specific; without one the step keeps the reason an earlier
    step of the recipe recorded, so a page the first step could not make out is still marked at the end of the recipe.

    :param data: Data of the version being made, to which the reason is added.
    :type data: dict[str, object]
    :param own: Reason the step found by itself, or None.
    :type own: ReviewReason | None
    :param facts: Data of the input version, which may hold the reason of an earlier step.
    :type facts: MetadataMap
    :returns: The reason, or None when neither this step nor an earlier one wants the page looked at.
    :rtype: ReviewReason | None
    """
    earlier = facts.get(VersionData.REVIEW)
    reason = own or (ReviewReason(earlier) if isinstance(earlier, str) else None)
    if reason is not None:
        data[VersionData.REVIEW] = reason.value
    return reason
