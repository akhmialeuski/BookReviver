"""Facts, limits, ordering and JPEG rules shared by every source format of the imaging adapters.

The facts a format reports beyond the typed ``ScanFacts`` fields go into ``extra`` and ``file_metadata`` under the keys
of ``FactKey``, so every format spells a key the same way. ``natural_order`` is the one order of the files of an
upload, which becomes the order of their sources in the book. ``only_file`` is the one rule that a PDF or an image
source is a single file. ``is_portable_jpeg`` is the one rule deciding whether a stored JPEG may be copied as the
image of a scan, whether it comes from a PDF page or from an image file.
"""

import enum
from operator import attrgetter
from typing import TYPE_CHECKING

from natsort import natsorted
from PIL import ExifTags, Image

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.enums import SourceKind

# Pillow refuses images above this many pixels as decompression bombs, and raises outright above twice as many.
# Its default of about 89 million pixels rejects a broadsheet newspaper scanned at 600 DPI, while an A1 sheet at
# 600 DPI is about 279 million pixels; this bound admits that and still rejects absurd headers. Pillow keeps it
# process-wide, and every format imports this module, so the bound holds for every file any of them opens. Pillow
# checks it when it opens a file, not when it seeks to a later TIFF frame, so ImageFormat holds each frame to it.
MAX_IMAGE_PIXELS: int = 300_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
MM_PER_INCH: float = 25.4
JPEG_FORMAT: str = 'JPEG'
# Pillow modes a browser and libvips read from a JPEG file as they are
PORTABLE_JPEG_MODES: frozenset[str] = frozenset({'L', 'RGB'})
# EXIF orientation of an image stored the way it is meant to be seen
UPRIGHT_ORIENTATION: int = 1


class FactKey(enum.StrEnum):
    """Keys of the source metadata and the scan extras the source formats report."""

    DOCUMENT_INFO = 'document_info'
    PDF_VERSION = 'pdf_version'
    PAGE_COUNT = 'page_count'
    HAS_OUTLINE = 'has_outline'
    OUTLINE_ENTRIES = 'outline_entries'
    HAS_XMP_METADATA = 'has_xmp_metadata'
    REPAIRED = 'repaired'
    FORMAT = 'format'
    FRAME_COUNT = 'frame_count'
    IMAGE_COUNT = 'image_count'
    ROTATION = 'rotation'
    TEXT_CHARS = 'text_chars'
    PILLOW_MODE = 'pillow_mode'
    EXIF = 'exif'


def natural_order(files: Sequence[Path]) -> list[Path]:
    """Return the files of an upload in book order, the natural sort of their names, so ``part2`` precedes ``part10``.

    :param files: Files of the upload in any order.
    :type files: Sequence[Path]
    :returns: The same paths in book order.
    :rtype: list[Path]
    """
    return natsorted(files, key=attrgetter('name'))


def only_file(files: Sequence[Path], *, kind: SourceKind) -> Path:
    """Return the file of a source of a kind whose every source is one file.

    :param files: Local paths of the files of the source.
    :type files: Sequence[Path]
    :param kind: Kind of the source, named in the error.
    :type kind: SourceKind
    :returns: The one file.
    :rtype: Path
    :raises ValueError: If there is not exactly one file, which only a caller that did not group the upload can cause.
    """
    if len(files) != 1:
        err_msg = f'A source of kind {kind} is one file, not {len(files)}.'
        raise ValueError(err_msg)
    return files[0]


def to_mm(length: float, *, units_per_inch: float) -> float:
    """Convert a length in points or pixels to millimetres, rounded to 0.1 mm.

    :param length: Length in points or pixels.
    :type length: float
    :param units_per_inch: Points or pixels per inch, 72 for points and the DPI for pixels.
    :type units_per_inch: float
    :returns: The length in millimetres.
    :rtype: float
    """
    return round(length / units_per_inch * MM_PER_INCH, 1)


def is_portable_jpeg(image: Image.Image) -> bool:
    """Return whether every reader shows the JPEG as its pixels are stored: gray or RGB, and upright.

    A browser turns a JPEG by its EXIF orientation while libvips ``dzsave`` and a PDF viewer do not, so an oriented
    JPEG would give tiles, thumbnail and page that disagree.

    :param image: Open image, of which only the header is read.
    :type image: Image.Image
    :returns: True when the file can be copied as the page image.
    :rtype: bool
    """
    orientation = int(image.getexif().get(ExifTags.Base.Orientation, UPRIGHT_ORIENTATION))
    return image.format == JPEG_FORMAT and image.mode in PORTABLE_JPEG_MODES and orientation == UPRIGHT_ORIENTATION
