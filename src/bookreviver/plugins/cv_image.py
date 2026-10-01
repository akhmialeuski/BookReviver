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

from bookreviver.domain.enums import ColorMode, VersionData
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
    return {**size.as_data(), COLOR_MODE_KEY: color_mode.value}
