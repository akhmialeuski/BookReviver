"""Image-set sources read with Pillow: the files of a directory of scans, one page per file, in book order.

A page image is described from its header alone: ``Image.open`` in Pillow reads the header lazily and decodes no
pixels, so a book of hundreds of large TIFFs is described without loading any of them. The accepted suffixes are those
of every ``FileType`` making an image set, so the upload rule and the reader never disagree on what a page image is.

A gray or RGB JPEG stored upright is copied as the page image byte for byte, as ``is_portable_jpeg`` decides. Every
other page image is turned upright and encoded by Pillow with the same pixel values, 16-bit gray scaled rather than
clipped, and its colour profile kept unless the conversion changes the colour space.
"""

import shutil
from typing import TYPE_CHECKING, Any, override

from PIL import ExifTags, Image, ImageOps

from bookreviver.adapters.imaging.common import JPEG_FORMAT, FactKey, is_portable_jpeg, natural_order, to_mm
from bookreviver.adapters.imaging.reader import SourceFormat
from bookreviver.domain.enums import ColorMode, FileType, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import PageFacts, SourceAnalysis

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

type ModeDepth = tuple[ColorMode, int | None]

IMAGE_FILE_TYPES: tuple[FileType, ...] = tuple(
    file_type for file_type in FileType if file_type.source_kind is SourceKind.IMAGES
)
IMAGE_SUFFIXES: frozenset[str] = frozenset(suffix for file_type in IMAGE_FILE_TYPES for suffix in file_type.suffixes)
IMAGE_TYPE_NAMES: str = ', '.join(file_type.label for file_type in IMAGE_FILE_TYPES)
PNG_FORMAT: str = 'PNG'
TIFF_FORMAT: str = 'TIFF'
DPI_KEY: str = 'dpi'
NO_DPI: tuple[float, float] = (0.0, 0.0)
# Key of ``Image.info`` present when a PNG carries an EXIF chunk
PNG_EXIF_INFO_KEY: str = 'exif'
# EXIF orientations 5 to 8 turn the stored image a quarter, so the page is as wide as the stored image is high
QUARTER_TURN_ORIENTATIONS: frozenset[int] = frozenset({5, 6, 7, 8})

# Colour mode and bits per component of the Pillow modes a scan can have
PILLOW_MODE_DEPTHS: Mapping[str, ModeDepth] = {
    '1': (ColorMode.BILEVEL, 1),
    'L': (ColorMode.GRAY, 8),
    'LA': (ColorMode.GRAY, 8),
    'RGB': (ColorMode.COLOR, 8),
    'RGBA': (ColorMode.COLOR, 8),
    'CMYK': (ColorMode.COLOR, 8),
    'YCbCr': (ColorMode.COLOR, 8),
    'LAB': (ColorMode.COLOR, 8),
    'P': (ColorMode.COLOR, 8),
}
# 'I;16', 'I;16B', 'I;16L' and 'I;16N' are all 16-bit grayscale
PILLOW_16_BIT_MODE_PREFIX: str = 'I;16'
SIXTEEN_BIT_GRAY_DEPTH: ModeDepth = (ColorMode.GRAY, 16)
UNKNOWN_DEPTH: ModeDepth = (ColorMode.UNKNOWN, None)
EXIF_TAGS: Sequence[ExifTags.Base] = (
    ExifTags.Base.Make,
    ExifTags.Base.Model,
    ExifTags.Base.Software,
    ExifTags.Base.DateTime,
)

# JPEG mode for the Pillow modes that do not become RGB
GRAY_MODE: str = 'L'
GRAY_JPEG_MODES: Mapping[str, str] = {'1': GRAY_MODE, 'L': GRAY_MODE, 'LA': GRAY_MODE, 'F': GRAY_MODE}
RGB_MODE: str = 'RGB'
INT32_MODE: str = 'I'
# Both 'I;16*' and 'I' hold samples wider than 8 bits
WIDE_GRAY_MODE_PREFIX: str = 'I'
# Maps a 16-bit sample onto 0..255; a plain conversion to 'L' clips instead of scaling
SIXTEEN_TO_EIGHT_BIT_SCALE: float = 1 / 256
ICC_PROFILE_KEY: str = 'icc_profile'
# Modes whose pixels Pillow converts into another colour space, so their embedded profile no longer describes them
PROFILE_CHANGING_MODES: frozenset[str] = frozenset({'CMYK', 'LAB', 'YCbCr'})


class ImageSetFormat(SourceFormat):
    """Describes a page image by its header alone, and copies or converts it as JPEG."""

    kind = SourceKind.IMAGES

    def __init__(self, *, jpeg_quality: int) -> None:
        """Encode converted pages at ``jpeg_quality``.

        :param jpeg_quality: JPEG quality from 1 to 100 for every page that is not copied.
        :type jpeg_quality: int
        """
        self._jpeg_quality = jpeg_quality

    @override
    def inspect(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe a set of page images in the natural order of their file names.

        :param files: Local paths of the page images, in any order.
        :type files: Sequence[Path]
        :returns: Facts of every page in book order, and the file count, total size and formats of the set.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the set is empty or a file is not a readable single-page image.
        """
        if not files:
            err_msg = f'No page images were uploaded. Upload at least one {IMAGE_TYPE_NAMES} file.'
            raise UnsupportedSourceError(err_msg)
        ordered = natural_order(files)
        pages = [_page_facts(path) for path in ordered]
        file_metadata: dict[str, Any] = {
            FactKey.FILE_COUNT: len(ordered),
            FactKey.TOTAL_SIZE_BYTES: sum(path.stat().st_size for path in ordered),
            FactKey.FORMATS: sorted({page.image_format for page in pages}),
        }
        return SourceAnalysis(kind=SourceKind.IMAGES, pages=pages, file_metadata=file_metadata)

    @override
    def extract(self, files: Sequence[Path], *, index: int, target: Path) -> None:
        """Copy an upright gray or RGB JPEG as it is, and encode any other page image upright as JPEG.

        The pixels keep their values and their colour profile, unless the conversion changes the colour space.

        :param files: Local paths of the page images, in any order.
        :type files: Sequence[Path]
        :param index: Position of the page in book order, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        """
        path = natural_order(files)[index]
        with Image.open(path) as image:
            if is_portable_jpeg(image):
                shutil.copyfile(path, target)
                return
            upright = ImageOps.exif_transpose(image)
            if upright.mode.startswith(WIDE_GRAY_MODE_PREFIX):
                scaled = upright.convert(INT32_MODE).point(lambda value: value * SIXTEEN_TO_EIGHT_BIT_SCALE)
                converted = scaled.convert(GRAY_MODE)
            else:
                converted = upright.convert(GRAY_JPEG_MODES.get(upright.mode, RGB_MODE))
            profile = None if image.mode in PROFILE_CHANGING_MODES else image.info.get(ICC_PROFILE_KEY)
            converted.save(target, format=JPEG_FORMAT, quality=self._jpeg_quality, icc_profile=profile)


def _page_facts(path: Path) -> PageFacts:
    """Describe one page image from its header, without decoding the pixel data.

    The size, resolution and physical size are those of the page as a viewer shows it, so an EXIF orientation that
    turns the stored image a quarter swaps them.

    :param path: Page image to describe.
    :type path: Path
    :returns: Size, resolution, colour mode, bit depth, format and physical size of the page, and its Pillow mode,
              file size and the scanner's EXIF tags.
    :rtype: PageFacts
    :raises UnsupportedSourceError: If the file has a wrong suffix, cannot be read, or holds several frames.
    """
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        err_msg = f'{path.name} is not a supported page image. Upload {IMAGE_TYPE_NAMES} files.'
        raise UnsupportedSourceError(err_msg)
    try:
        image = Image.open(path)
    except (OSError, Image.DecompressionBombError) as error:
        err_msg = f'{path.name} cannot be read as an image: the file is damaged or too large. Replace this file.'
        raise UnsupportedSourceError(err_msg) from error

    with image:
        if getattr(image, 'n_frames', 1) > 1:
            err_msg = f'{path.name} holds several pages. Split it into one image file per page and upload again.'
            raise UnsupportedSourceError(err_msg)
        color_mode, bits = (
            SIXTEEN_BIT_GRAY_DEPTH
            if image.mode.startswith(PILLOW_16_BIT_MODE_PREFIX)
            else PILLOW_MODE_DEPTHS.get(image.mode, UNKNOWN_DEPTH)
        )
        # Pillow decodes a whole PNG to look for EXIF placed after the pixel data, so read only the header chunk
        exif = image.getexif() if image.format != PNG_FORMAT or PNG_EXIF_INFO_KEY in image.info else {}
        # Pillow reports 1 DPI for a TIFF without resolution tags, which would make the page metres wide
        dpi = image.info.get(DPI_KEY) if image.format != TIFF_FORMAT or ExifTags.Base.XResolution in exif else None
        dpi_x, dpi_y = (round(float(value), 1) if value > 0 else None for value in dpi or NO_DPI)
        width_px, height_px = image.size
        if exif.get(ExifTags.Base.Orientation) in QUARTER_TURN_ORIENTATIONS:
            width_px, height_px, dpi_x, dpi_y = height_px, width_px, dpi_y, dpi_x
        return PageFacts(
            source_file=path.name,
            width_px=width_px,
            height_px=height_px,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
            color_mode=color_mode,
            bits_per_component=bits,
            image_format=image.format or '',
            width_mm=to_mm(width_px, units_per_inch=dpi_x) if dpi_x else None,
            height_mm=to_mm(height_px, units_per_inch=dpi_y) if dpi_y else None,
            extra={
                FactKey.PILLOW_MODE: image.mode,
                FactKey.FILE_SIZE_BYTES: path.stat().st_size,
                FactKey.EXIF: {tag.name: str(exif[tag]) for tag in EXIF_TAGS if tag in exif},
            },
        )
