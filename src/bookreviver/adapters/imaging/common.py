"""Facts, limits and JPEG rules shared by every source format of the imaging adapters.

The facts a format reports beyond the typed ``ScanFacts`` fields go into ``extra`` and ``file_metadata`` under the keys
of ``FactKey``, so every format spells a key the same way. No function here sorts the files of an upload, because the
order the user gave them is the order of the book. ``only_file`` is the one rule that a PDF or an image
source is a single file. ``is_portable_jpeg`` is the one rule deciding whether a stored JPEG may be copied as the
image of a scan, whether it comes from a PDF page or from an image file. ``write_full`` is the one place that writes a
page image in the format the caller asked for, so every source format encodes a JPEG and a PNG alike.
"""

import enum
from typing import TYPE_CHECKING

from PIL import ExifTags, Image

from bookreviver.domain.enums import Rendition

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
PNG_FORMAT: str = 'PNG'
# Pillow modes of a bilevel and of a gray image; a JPEG holds no bit depth of one, so a bilevel image is written gray
BILEVEL_MODE: str = '1'
GRAY_MODE: str = 'L'
# Key of ``Image.info`` and of the save options holding a colour profile
ICC_PROFILE_KEY: str = 'icc_profile'
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
    DJVU_KIND = 'djvu_kind'
    COMPONENT_COUNT = 'component_count'
    DJVU_CHUNKS = 'djvu_chunks'


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
    JPEG would give tiles, thumbnail and page that disagree. The EXIF block is read only once the image is known to be
    a gray or RGB JPEG, whose EXIF Pillow parsed with the header: for a PNG, ``getexif`` reads the whole file to look
    for EXIF placed after the pixel data.

    :param image: Open image, of which only the header is read.
    :type image: Image.Image
    :returns: True when the file can be copied as the page image.
    :rtype: bool
    """
    if image.format != JPEG_FORMAT or image.mode not in PORTABLE_JPEG_MODES:
        return False
    return int(image.getexif().get(ExifTags.Base.Orientation, UPRIGHT_ORIENTATION)) == UPRIGHT_ORIENTATION


def write_full(
    image: Image.Image, target: Path, *, full: Rendition, jpeg_quality: int, icc_profile: bytes | None = None
) -> None:
    """Write a page image at ``target`` as a JPEG or a PNG, whichever ``full`` names.

    A PNG keeps the mode of the image, so a bilevel image becomes a 1-bit PNG with exactly its two values, and gray,
    RGB and their profile are stored without loss. A JPEG holds no bit depth of one, so a bilevel image is written gray.
    The profile is written exactly as given: ``None`` writes none, even if the image carries one.

    :param image: Page image in a mode a JPEG or a PNG can hold: ``1``, ``L`` or ``RGB``.
    :type image: Image.Image
    :param target: Path to write the image at.
    :type target: Path
    :param full: Format to write, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
    :type full: Rendition
    :param jpeg_quality: JPEG quality from 1 to 100, unused for a PNG.
    :type jpeg_quality: int
    :param icc_profile: Colour profile to embed, or None for none.
    :type icc_profile: bytes | None
    :raises ValueError: If ``full`` is not a format of the ``full`` image, which only a caller that skipped the
                        domain's choice of the format can cause.
    """
    match full:
        case Rendition.FULL_PNG:
            image.save(target, format=PNG_FORMAT, icc_profile=icc_profile)
        case Rendition.FULL_JPEG:
            jpeg = image.convert(GRAY_MODE) if image.mode == BILEVEL_MODE else image
            jpeg.save(target, format=JPEG_FORMAT, quality=jpeg_quality, icc_profile=icc_profile)
        case _:
            err_msg = f'{full} is not a format of the full image.'
            raise ValueError(err_msg)
