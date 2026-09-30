"""Image sources read with Pillow: one image file is one source, and each of its frames is one scan.

A JPEG, JPEG 2000 or PNG file holds one scan. A TIFF file holds one scan per frame, numbered from 0, so a multi-page
TIFF is one source of many scans. A directory of scans is as many sources as it has files, whose scans join the book
in the order of the upload.

A scan is described from its header alone: ``Image.open`` in Pillow reads the header lazily and decodes no pixels, and
seeking to a TIFF frame reads only that frame's header, so hundreds of large TIFFs are described without loading any
of them. The accepted suffixes are those of every ``FileType`` making an image source, so the upload rule and the
reader never disagree on what an image file is.

Pillow treats the first frame of a file and the later frames of a TIFF differently, and ``_ImageFile`` evens that out.
``Image.open`` refuses an unreadable header, or more pixels than twice ``MAX_IMAGE_PIXELS``, but only for the first
frame. Counting and seeking the later frames parse their headers without that care: a broken header escapes as a bare
``SyntaxError`` or ``EOFError``, and the pixel bound is not applied at all. Pillow also keeps one ``info`` for the
whole file and only adds to it on a seek, so a colour profile or a resolution a frame does not have stays behind from
an earlier frame. ``_ImageFile`` therefore reads every header behind one error boundary, holds every frame to the
pixel bound, and reads the resolution and the colour profile of a TIFF frame from that frame's own tags.

A gray or RGB JPEG stored upright is copied as the image of its scan byte for byte, as ``is_portable_jpeg`` decides.
Every other scan is turned upright and encoded by Pillow with the same pixel values, 16-bit gray scaled rather than
clipped, and its own colour profile kept unless the conversion changes the colour space.
"""

import shutil
import struct
from typing import TYPE_CHECKING, Any, Self, override

from PIL import ExifTags, Image, ImageOps, TiffImagePlugin

from bookreviver.adapters.imaging.common import (
    JPEG_FORMAT,
    MAX_IMAGE_PIXELS,
    FactKey,
    is_portable_jpeg,
    only_file,
    to_mm,
)
from bookreviver.adapters.imaging.reader import SourceFormat
from bookreviver.domain.enums import ColorMode, FileType, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import ScanFacts, SourceAnalysis

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from types import TracebackType

type ModeDepth = tuple[ColorMode, int | None]
type Dpi = tuple[float | None, float | None]

IMAGE_FILE_TYPES: tuple[FileType, ...] = tuple(
    file_type for file_type in FileType if file_type.source_kind is SourceKind.IMAGE
)
IMAGE_SUFFIXES: frozenset[str] = frozenset(suffix for file_type in IMAGE_FILE_TYPES for suffix in file_type.suffixes)
IMAGE_TYPE_NAMES: str = ', '.join(file_type.label for file_type in IMAGE_FILE_TYPES)
PNG_FORMAT: str = 'PNG'
DPI_KEY: str = 'dpi'
NO_DPI: tuple[float, float] = (0.0, 0.0)
# What Pillow raises for a header it cannot read: Image.open turns SyntaxError, IndexError, TypeError and struct.error
# into an OSError for the first frame, counting and seeking later TIFF frames raise them bare, and a frame chain it
# cannot follow raises EOFError or ValueError
UNREADABLE_IMAGE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    SyntaxError,
    IndexError,
    TypeError,
    struct.error,
    EOFError,
    ValueError,
    Image.DecompressionBombError,
)
# Image.open refuses a first frame above this, but no later TIFF frame is checked on a seek, and an uncompressed one
# is even loaded without a check, so every frame is held to it here
MAX_FRAME_PIXELS: int = 2 * MAX_IMAGE_PIXELS
# Factor from dots per TIFF resolution unit to dots per inch: the inch, the default when the tag is absent, and the
# centimetre; the unit 1, no absolute unit, gives no resolution
TIFF_INCH_UNIT: int = 2
TIFF_CENTIMETRE_UNIT: int = 3
TIFF_UNIT_TO_DPI: Mapping[int, float] = {TIFF_INCH_UNIT: 1.0, TIFF_CENTIMETRE_UNIT: 2.54}
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
        with _ImageFile(path) as file:
            frame_count = file.frame_count
            scans: list[ScanFacts] = []
            for number in range(frame_count):
                file.seek(number)
                scans.append(_scan_facts(file))
            file_metadata: dict[str, Any] = {FactKey.FORMAT: file.image.format or '', FactKey.FRAME_COUNT: frame_count}
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
        :raises UnsupportedSourceError: If the file or the frame cannot be read as an image.
        :raises IndexError: If the file has fewer frames than ``number + 1``.
        :raises ValueError: If the source is not exactly one file.
        """
        path = only_file(files, kind=self.kind)
        with _ImageFile(path) as file:
            frame_count = file.frame_count
            if not 0 <= number < frame_count:
                err_msg = f'{path.name} has no frame {number}: it holds {frame_count} frames.'
                raise IndexError(err_msg)
            file.seek(number)
            image = file.image
            if is_portable_jpeg(image):
                shutil.copyfile(path, target)
                return
            upright = ImageOps.exif_transpose(image)
            if upright.mode.startswith(WIDE_GRAY_MODE_PREFIX):
                scaled = upright.convert(INT32_MODE).point(lambda value: value * SIXTEEN_TO_EIGHT_BIT_SCALE)
                converted = scaled.convert(GRAY_MODE)
            else:
                converted = upright.convert(GRAY_JPEG_MODES.get(upright.mode, RGB_MODE))
            profile = None if image.mode in PROFILE_CHANGING_MODES else file.icc_profile
            converted.save(target, format=JPEG_FORMAT, quality=self._jpeg_quality, icc_profile=profile)


class _ImageFile:
    """An image file open in Pillow, whose every header is read behind one error boundary, one frame at a time.

    :ivar image: The open image, positioned at the frame last sought; the first frame until then.
    """

    def __init__(self, path: Path) -> None:
        """Read the image file at ``path`` once the context opens.

        :param path: Image file to read.
        :type path: Path
        """
        self._path = path
        self.image: Image.Image

    def __enter__(self) -> Self:
        """Open the file, reading the header of its first frame.

        :returns: This file, positioned at its first frame.
        :rtype: Self
        :raises UnsupportedSourceError: If the header cannot be read, or the first frame has too many pixels.
        """
        try:
            self.image = Image.open(self._path)
        except UNREADABLE_IMAGE_ERRORS as error:
            err_msg = (
                f'{self._path.name} cannot be read as an image: the file is damaged or too large. Replace this file.'
            )
            raise UnsupportedSourceError(err_msg) from error
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the file.

        :param exc_type: Type of the exception leaving the context, or None.
        :type exc_type: type[BaseException] | None
        :param exc_value: Exception leaving the context, or None.
        :type exc_value: BaseException | None
        :param traceback: Traceback of the exception leaving the context, or None.
        :type traceback: TracebackType | None
        """
        self.image.close()

    @property
    def frame_count(self) -> int:
        """The number of scans the file holds: every frame of a TIFF, and one for any other format.

        A JPEG may carry a second frame, such as the preview of a multi-picture file, which is no page of the book.
        Counting the frames of a TIFF reads the header of every frame, so a broken one is refused here.
        """
        if not isinstance(self.image, TiffImagePlugin.TiffImageFile):
            return 1
        try:
            return self.image.n_frames
        except UNREADABLE_IMAGE_ERRORS as error:
            err_msg = f'{self._path.name} cannot be read as an image: a frame header is damaged. Replace this file.'
            raise UnsupportedSourceError(err_msg) from error

    @property
    def dpi(self) -> Dpi:
        """The resolution of the current frame in dots per inch, None for an axis without one.

        A TIFF frame is read from its own tags, since Pillow keeps the resolution an earlier frame set and reports
        1 DPI for a frame without resolution tags, which would make the scan metres wide.
        """
        image = self.image
        if isinstance(image, TiffImagePlugin.TiffImageFile):
            tags = image.tag_v2
            to_dpi = TIFF_UNIT_TO_DPI.get(tags.get(TiffImagePlugin.RESOLUTION_UNIT, TIFF_INCH_UNIT))
            has_resolution = TiffImagePlugin.X_RESOLUTION in tags and TiffImagePlugin.Y_RESOLUTION in tags
            values = (
                (float(tags[TiffImagePlugin.X_RESOLUTION]) * to_dpi, float(tags[TiffImagePlugin.Y_RESOLUTION]) * to_dpi)
                if to_dpi and has_resolution
                else NO_DPI
            )
        else:
            values = image.info.get(DPI_KEY, NO_DPI)
        dpi_x, dpi_y = (round(float(value), 1) if value > 0 else None for value in values)
        return dpi_x, dpi_y

    @property
    def icc_profile(self) -> bytes | None:
        """The colour profile of the current frame, or None when it has none.

        A TIFF frame is read from its own tags, since Pillow keeps the profile an earlier frame had.
        """
        if isinstance(self.image, TiffImagePlugin.TiffImageFile):
            profile = self.image.tag_v2.get(TiffImagePlugin.ICCPROFILE)
        else:
            profile = self.image.info.get(ICC_PROFILE_KEY)
        return bytes(profile) if profile else None

    def seek(self, number: int) -> None:
        """Move to frame ``number``, reading its header, and hold it to the pixel bound of the first frame.

        :param number: Number of the frame, at least 0 and below ``frame_count``.
        :type number: int
        :raises UnsupportedSourceError: If the header of the frame cannot be read, or the frame has more pixels than
                                        ``MAX_FRAME_PIXELS``.
        """
        err_msg = (
            f'{self._path.name} cannot be read as an image: frame {number} is damaged or too large. Replace this file.'
        )
        try:
            self.image.seek(number)
        except UNREADABLE_IMAGE_ERRORS as error:
            raise UnsupportedSourceError(err_msg) from error
        width, height = self.image.size
        if width * height > MAX_FRAME_PIXELS:
            raise UnsupportedSourceError(err_msg)


def _scan_facts(file: _ImageFile) -> ScanFacts:
    """Describe the current frame of an open image file from its header, without decoding the pixel data.

    The size, resolution and physical size are those of the scan as a viewer shows it, so an EXIF orientation that
    turns the stored image a quarter swaps them.

    :param file: Open image file, positioned at the frame to describe.
    :type file: _ImageFile
    :returns: Size, resolution, colour mode, bit depth, format and physical size of the scan, and its Pillow mode and
              the scanner's EXIF tags.
    :rtype: ScanFacts
    """
    image = file.image
    color_mode, bits = (
        SIXTEEN_BIT_GRAY_DEPTH
        if image.mode.startswith(PILLOW_16_BIT_MODE_PREFIX)
        else PILLOW_MODE_DEPTHS.get(image.mode, UNKNOWN_DEPTH)
    )
    # Pillow decodes a whole PNG to look for EXIF placed after the pixel data, so read only the header chunk
    exif = image.getexif() if image.format != PNG_FORMAT or PNG_EXIF_INFO_KEY in image.info else {}
    dpi_x, dpi_y = file.dpi
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
