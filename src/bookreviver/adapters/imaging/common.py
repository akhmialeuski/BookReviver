"""Facts, limits and JPEG rules shared by every source format of the imaging adapters.

The facts a format reports beyond the typed ``PageFacts`` fields go into ``extra`` and ``file_metadata`` under the keys
of ``FactKey``, so every format spells a key the same way. ``is_portable_jpeg`` is the one rule deciding whether a
stored JPEG may be copied as the page image, whether it comes from a PDF page or from a page image.
"""

import enum

from PIL import ExifTags, Image

# Pillow refuses images above this many pixels as decompression bombs, and raises outright above twice as many.
# Its default of about 89 million pixels rejects a broadsheet newspaper scanned at 600 DPI, while an A1 sheet at
# 600 DPI is about 279 million pixels; this bound admits that and still rejects absurd headers. Pillow keeps it
# process-wide, and every format imports this module, so the bound holds for every page any of them opens.
MAX_IMAGE_PIXELS: int = 300_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
MM_PER_INCH: float = 25.4
SHA256: str = 'sha256'
JPEG_FORMAT: str = 'JPEG'
# Pillow modes a browser and libvips read from a JPEG file as they are
PORTABLE_JPEG_MODES: frozenset[str] = frozenset({'L', 'RGB'})
# EXIF orientation of an image stored the way it is meant to be seen
UPRIGHT_ORIENTATION: int = 1


class FactKey(enum.StrEnum):
    """Keys of the file metadata and the page extras the source formats report."""

    DOCUMENT_INFO = 'document_info'
    PDF_VERSION = 'pdf_version'
    PAGE_COUNT = 'page_count'
    HAS_OUTLINE = 'has_outline'
    OUTLINE_ENTRIES = 'outline_entries'
    HAS_XMP_METADATA = 'has_xmp_metadata'
    REPAIRED = 'repaired'
    SHA256 = 'sha256'
    FILE_SIZE_BYTES = 'file_size_bytes'
    FILE_COUNT = 'file_count'
    TOTAL_SIZE_BYTES = 'total_size_bytes'
    FORMATS = 'formats'
    IMAGE_COUNT = 'image_count'
    ROTATION = 'rotation'
    TEXT_CHARS = 'text_chars'
    PILLOW_MODE = 'pillow_mode'
    EXIF = 'exif'


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
