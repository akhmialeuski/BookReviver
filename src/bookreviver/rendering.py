"""Render page images for the browser and cache them on disk.

Synchronous and CPU bound; web handlers call ``render_page`` through a worker thread.
"""

import enum
from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.models import SourceKind


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
    raise NotImplementedError
