"""Image sources read with Pillow: one image file is one source, and each of its frames is one scan.

A JPEG, JPEG 2000 or PNG file holds one scan. A TIFF file holds one scan per frame, numbered from 0, so a multi-page
TIFF is one source of many scans. A directory of scans is as many sources as it has files, whose scans join the book
in the order of the upload.

A scan is described from its header alone: ``Image.open`` in Pillow reads the header lazily and decodes no pixels, and
seeking to a TIFF frame reads only that frame's header, so hundreds of large TIFFs are described without loading any
of them. The accepted suffixes are those of every ``FileType`` making an image source, so the upload rule and the
reader never disagree on what an image file is.

A gray or RGB JPEG stored upright is copied as the image of its scan byte for byte, as ``is_portable_jpeg`` decides.
Every other scan is turned upright and encoded by Pillow with the same pixel values, 16-bit gray scaled rather than
clipped, and its colour profile kept unless the conversion changes the colour space.
"""

import shutil
from typing import TYPE_CHECKING, Any, override

from PIL import ExifTags, Image, ImageOps, TiffImagePlugin

from bookreviver.adapters.imaging.common import JPEG_FORMAT, FactKey, is_portable_jpeg, only_file, to_mm
from bookreviver.adapters.imaging.reader import SourceFormat
from bookreviver.domain.enums import ColorMode, FileType, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import ScanFacts, SourceAnalysis

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

type ModeDepth = tuple[ColorMode, int | None]

IMAGE_FILE_TYPES: tuple[FileType, ...] = tuple(
    file_type for file_type in FileType if file_type.source_kind is SourceKind.IMAGE
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


class ImageFormat(SourceFormat):
    """Describes each frame of an image file by its header alone, and copies or converts it as JPEG."""

    kind = SourceKind.IMAGE

    def __init__(self, *, jpeg_quality: int) -> None:
        """Encode converted scans at ``jpeg_quality``.

        :param jpeg_quality: JPEG quality from 1 to 100 for every scan that is not copied.
        :type jpeg_quality: int
        """
        self._jpeg_quality = jpeg_quality

    @override
    def inspect(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe every frame of one image file from its header.

        :param files: Local path of the image file, the one file of the source.
        :type files: Sequence[Path]
        :returns: Facts of every frame in the order of the file, and the format and frame count of the file.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the file has a wrong suffix or cannot be read as an image.
        :raises ValueError: If the source is not exactly one file.
        """
        path = only_file(files, kind=self.kind)
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            err_msg = f'{path.name} is not a supported image file. Upload {IMAGE_TYPE_NAMES} files.'
            raise UnsupportedSourceError(err_msg)
        try:
            image = Image.open(path)
        except (OSError, Image.DecompressionBombError) as error:
            err_msg = f'{path.name} cannot be read as an image: the file is damaged or too large. Replace this file.'
            raise UnsupportedSourceError(err_msg) from error
        with image:
            frame_count = _frame_count(image)
            scans: list[ScanFacts] = []
            for number in range(frame_count):
                image.seek(number)
                scans.append(_scan_facts(image))
            file_metadata: dict[str, Any] = {FactKey.FORMAT: image.format or '', FactKey.FRAME_COUNT: frame_count}
        return SourceAnalysis(kind=SourceKind.IMAGE, scans=scans, file_metadata=file_metadata)

    @override
    def extract(self, files: Sequence[Path], *, number: int, target: Path) -> None:
        """Copy an upright gray or RGB JPEG as it is, and encode any other frame upright as JPEG.

        The pixels keep their values and their colour profile, unless the conversion changes the colour space.

        :param files: Local path of the image file, the one file of the source.
        :type files: Sequence[Path]
        :param number: Number of the frame in the file, starting at 0; only a TIFF file has more than one.
        :type number: int
        :param target: Path to write the JPEG at.
        :type target: Path
        :raises IndexError: If the file has fewer frames than ``number + 1``.
        :raises ValueError: If the source is not exactly one file.
        """
        path = only_file(files, kind=self.kind)
        with Image.open(path) as image:
            frame_count = _frame_count(image)
            if not 0 <= number < frame_count:
                err_msg = f'{path.name} has no frame {number}: it holds {frame_count} frames.'
                raise IndexError(err_msg)
            image.seek(number)
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


def _frame_count(image: Image.Image) -> int:
    """Return the number of scans an open image file holds: every frame of a TIFF, and one for any other format.

    A JPEG may carry a second frame, such as the preview of a multi-picture file, which is no page of the book.

    :param image: Open image file, of which only the headers are read.
    :type image: Image.Image
    :returns: The number of scans, at least 1.
    :rtype: int
    """
    return image.n_frames if isinstance(image, TiffImagePlugin.TiffImageFile) else 1


def _scan_facts(image: Image.Image) -> ScanFacts:
    """Describe the current frame of an open image file from its header, without decoding the pixel data.

    The size, resolution and physical size are those of the scan as a viewer shows it, so an EXIF orientation that
    turns the stored image a quarter swaps them.

    :param image: Open image file, positioned at the frame to describe.
    :type image: Image.Image
    :returns: Size, resolution, colour mode, bit depth, format and physical size of the scan, and its Pillow mode and
              the scanner's EXIF tags.
    :rtype: ScanFacts
    """
    color_mode, bits = (
        SIXTEEN_BIT_GRAY_DEPTH
        if image.mode.startswith(PILLOW_16_BIT_MODE_PREFIX)
        else PILLOW_MODE_DEPTHS.get(image.mode, UNKNOWN_DEPTH)
    )
    # Pillow decodes a whole PNG to look for EXIF placed after the pixel data, so read only the header chunk
    exif = image.getexif() if image.format != PNG_FORMAT or PNG_EXIF_INFO_KEY in image.info else {}
    # Pillow reports 1 DPI for a TIFF without resolution tags, which would make the scan metres wide
    dpi = image.info.get(DPI_KEY) if image.format != TIFF_FORMAT or ExifTags.Base.XResolution in exif else None
    dpi_x, dpi_y = (round(float(value), 1) if value > 0 else None for value in dpi or NO_DPI)
    width_px, height_px = image.size
    if exif.get(ExifTags.Base.Orientation) in QUARTER_TURN_ORIENTATIONS:
        width_px, height_px, dpi_x, dpi_y = height_px, width_px, dpi_y, dpi_x
    return ScanFacts(
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
            FactKey.EXIF: {tag.name: str(exif[tag]) for tag in EXIF_TAGS if tag in exif},
        },
    )
