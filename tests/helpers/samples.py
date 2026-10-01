"""Generated page images for the tests of the image processors, which use no file kept in the repository.

A page of text is drawn as lines of black blocks, the words, on white paper, which is what the projection of the ink on
the rows of a page cares about. A spread is two such pages side by side with a dark strip between them, the gutter. The
images are made with Pillow alone, so a test module that only builds them runs without OpenCV.
"""

import io
import random
from typing import TYPE_CHECKING

from PIL import Image, ImageChops, ImageDraw

if TYPE_CHECKING:
    from pathlib import Path

# Why the tests of the OpenCV plugins are skipped on a machine that lacks it
CV_MISSING: str = 'OpenCV is not installed; install the optional group with `uv sync --extra cv`.'
PAPER: int = 255
INK: int = 0
LINE_PITCH_PX: int = 28
WORD_HEIGHT_PX: int = 12
MARGIN_PX: int = 60
WORD_WIDTH_RANGE_PX: tuple[int, int] = (20, 90)
WORD_GAP_RANGE_PX: tuple[int, int] = (8, 18)
GUTTER_HALF_WIDTH_PX: int = 14
GUTTER_SHADE: int = 90
MARKER_COLOR: tuple[int, int, int] = (220, 20, 20)
MARKER_RADIUS_PX: int = 9
# How much redder than green a pixel is where the landmark is, which paper, ink and the gutter are not
REDNESS_LIMIT: int = 100


def text_page(width_px: int, height_px: int, *, seed: int = 7) -> Image.Image:
    """Draw a gray page of lines of words.

    :param width_px: Width of the page in pixels.
    :type width_px: int
    :param height_px: Height of the page in pixels.
    :type height_px: int
    :param seed: Seed of the widths of the words, so a page is the same every time.
    :type seed: int
    :returns: The page, in gray, with black words on white paper.
    :rtype: Image.Image
    """
    chance = random.Random(seed)
    page = Image.new('L', (width_px, height_px), PAPER)
    draw = ImageDraw.Draw(page)
    for top in range(MARGIN_PX, height_px - MARGIN_PX, LINE_PITCH_PX):
        left = MARGIN_PX
        while left < width_px - MARGIN_PX:
            word = chance.randint(*WORD_WIDTH_RANGE_PX)
            right = min(left + word, width_px - MARGIN_PX)
            draw.rectangle((left, top, right, top + WORD_HEIGHT_PX), fill=INK)
            left = right + chance.randint(*WORD_GAP_RANGE_PX)
    return page


def turned(page: Image.Image, degrees: float) -> Image.Image:
    """Turn a page about its centre as a skewed scan is, counter-clockwise, leaving white corners.

    :param page: The page to turn.
    :type page: Image.Image
    :param degrees: Angle in degrees, counter-clockwise.
    :type degrees: float
    :returns: The turned page, of the same size.
    :rtype: Image.Image
    """
    return page.rotate(degrees, resample=Image.Resampling.BICUBIC, fillcolor=PAPER)


def spread(page_width_px: int, height_px: int, *, tilt: float = 0.0) -> Image.Image:
    """Draw a colour scan of two facing pages with the gutter between them, the left page optionally skewed.

    :param page_width_px: Width of each page in pixels, so the gutter is at this distance from the left edge.
    :type page_width_px: int
    :param height_px: Height of the scan in pixels.
    :type height_px: int
    :param tilt: Angle in degrees, counter-clockwise, the left page is turned by.
    :type tilt: float
    :returns: The scan, in colour.
    :rtype: Image.Image
    """
    scan = Image.new('RGB', (2 * page_width_px, height_px), (PAPER, PAPER, PAPER))
    scan.paste(turned(text_page(page_width_px, height_px, seed=1), tilt), (0, 0))
    scan.paste(text_page(page_width_px, height_px, seed=2), (page_width_px, 0))
    draw = ImageDraw.Draw(scan)
    draw.rectangle(
        (
            page_width_px - GUTTER_HALF_WIDTH_PX,
            0,
            page_width_px + GUTTER_HALF_WIDTH_PX,
            height_px,
        ),
        fill=(GUTTER_SHADE, GUTTER_SHADE, GUTTER_SHADE),
    )
    return scan


def mark(image: Image.Image, centre: tuple[int, int]) -> None:
    """Draw a red disc, a landmark that a test finds again after the image went through a processor.

    :param image: Colour image to draw on.
    :type image: Image.Image
    :param centre: Centre of the disc in pixels.
    :type centre: tuple[int, int]
    """
    x, y = centre
    ImageDraw.Draw(image).ellipse(
        (x - MARKER_RADIUS_PX, y - MARKER_RADIUS_PX, x + MARKER_RADIUS_PX, y + MARKER_RADIUS_PX), fill=MARKER_COLOR
    )


def find_mark(image: Image.Image) -> tuple[float, float]:
    """Find the centre of the red disc ``mark`` drew, by the box of the pixels that are red.

    :param image: Colour image holding one disc.
    :type image: Image.Image
    :returns: The centre of the disc in pixels, as the middle of the pixels it covers.
    :rtype: tuple[float, float]
    :raises ValueError: If the image holds no red pixel.
    """
    red, green, _blue = image.convert('RGB').split()
    redness = ImageChops.subtract(red, green).point(lambda value: PAPER if value > REDNESS_LIMIT else INK)
    if (box := redness.getbbox()) is None:
        err_msg = 'The image holds no landmark.'
        raise ValueError(err_msg)
    left, top, right, bottom = box
    return (left + right - 1) / 2, (top + bottom - 1) / 2


def save(image: Image.Image, path: Path) -> Path:
    """Save an image as a PNG, which loses nothing.

    :param image: The image to save.
    :type image: Image.Image
    :param path: Path of the file to make.
    :type path: Path
    :returns: The path.
    :rtype: Path
    """
    image.save(path, format='PNG')
    return path


def png_bytes(image: Image.Image) -> bytes:
    """Encode an image as a PNG, which loses nothing.

    :param image: The image to encode.
    :type image: Image.Image
    :returns: The content of the PNG file.
    :rtype: bytes
    """
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
