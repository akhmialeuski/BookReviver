"""Render page images for the browser and cache them on disk.

Synchronous and CPU bound; web handlers call ``render_page`` through a worker thread.
"""

import enum
import tempfile
from pathlib import Path

import pymupdf
from attrs import frozen
from PIL import Image

from bookreviver.analysis import MAX_IMAGE_PIXELS
from bookreviver.models import SourceKind

# Same decompression-bomb bound as the analysis that admitted the image
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

WEBP_FORMAT: str = 'WEBP'
WEBP_QUALITY: int = 85
TEMP_SUFFIX: str = '.tmp'
JPEG_FORMAT: str = 'JPEG'
BILEVEL_MODE: str = '1'
GRAY_MODE: str = 'L'
INT32_MODE: str = 'I'
# Both 'I;16*' and 'I' hold samples wider than 8 bits
WIDE_GRAY_MODE_PREFIX: str = 'I'
# Maps a 16-bit sample onto 0..255; a plain conversion to 'L' clips instead of scaling
SIXTEEN_TO_EIGHT_BIT_SCALE: float = 1 / 256


class RenderVariant(enum.StrEnum):
    """Size class of a rendered page, identified in URLs by its value."""

    THUMBNAIL = 'thumb'
    PREVIEW = 'preview'

    @property
    def long_side_px(self) -> int:
        """The length of the longer side of the rendered image."""
        return VARIANT_LONG_SIDE_PX[self]


VARIANT_LONG_SIDE_PX: dict[RenderVariant, int] = {
    RenderVariant.THUMBNAIL: 320,
    RenderVariant.PREVIEW: 2000,
}


@frozen(kw_only=True)
class RenderRequest:
    """One page to render: a PDF with a page index, or a single image file."""

    kind: SourceKind
    # The PDF file for a PDF source, the page's own image file for an image set
    source_path: Path
    page_index: int
    variant: RenderVariant
    cache_dir: Path


def render_page(request: RenderRequest) -> Path:
    """Return a WebP file of the page, rendering it into the cache only when missing.

    Writes go to a temporary file renamed into place, so concurrent requests never see a partial image.
    """
    output = request.cache_dir / f'{request.variant}-{request.page_index:05d}.webp'
    if output.exists():
        return output
    request.cache_dir.mkdir(parents=True, exist_ok=True)
    long_side = request.variant.long_side_px
    match request.kind:
        case SourceKind.PDF:
            with pymupdf.open(request.source_path) as document:
                page = document[request.page_index]
                zoom = long_side / max(page.rect.width, page.rect.height)
                image = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).pil_image()
            _write_webp(image, output=output)
        case SourceKind.IMAGES:
            with Image.open(request.source_path) as source:
                image = source
                if image.format == JPEG_FORMAT:
                    # Decode at a reduced scale that still covers the long side
                    image.draft(None, (long_side, long_side))
                if image.mode == BILEVEL_MODE:
                    image = image.convert(GRAY_MODE)
                elif image.mode.startswith(WIDE_GRAY_MODE_PREFIX):
                    image = image.convert(INT32_MODE).point(lambda value: value * SIXTEEN_TO_EIGHT_BIT_SCALE)
                    image = image.convert(GRAY_MODE)
                image.thumbnail((long_side, long_side))
                _write_webp(image, output=output)
    return output


def _write_webp(image: Image.Image, *, output: Path) -> None:
    """Encode the image as WebP next to ``output`` and atomically move it into place."""
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=output.stem,
        suffix=TEMP_SUFFIX,
        delete_on_close=False,
    ) as temp_file:
        image.save(temp_file, format=WEBP_FORMAT, quality=WEBP_QUALITY)
        temp_file.close()
        # The context exit then finds no temporary file left to delete
        Path(temp_file.name).replace(output)
