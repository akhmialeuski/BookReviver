"""Schema of a page of the book: its place in the book, its kind and the images of its base version.

A page is identified by its ``PageId`` and its place in the book is its ``position``, counted from zero over the pages
of the project in book order. The fractional order key that keeps the order never leaves the server, so a client cannot
build a page order from it. ``images`` shows the base version of the page, which is the only version recorded until
the processing stages write their own, and is empty for a page that has no image yet: a placeholder waiting for a scan,
or a page whose images are still being cut.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Self

from bookreviver.api.schemas.base import ResponseModel
from bookreviver.api.schemas.images import ImagePathsSchema
from bookreviver.domain.enums import PageKind, PageOrigin
from bookreviver.domain.ids import PageId, ScanId
from bookreviver.domain.keys import ProjectKeys

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.entities import PageOverview


class PageSchema(ResponseModel):
    """A page of a book.

    :ivar id: Identifier of the page, which stays the same when the page is moved.
    :ivar position: Place of the page in the book from zero, counted over every page, excluded ones included.
    :ivar label: Printed number, such as ``xii`` or ``12``, or empty for an unnumbered page.
    :ivar kind: Role of the page in the book.
    :ivar origin: Where the image of the page comes from.
    :ivar scan_id: Scan the page was cut from, or None for a blank leaf, a placeholder, or a page whose source was
                   deleted.
    :ivar slot: Part of the scan the page shows: 0 the whole scan, 1 and 2 the halves of a spread.
    :ivar included: Whether the page is part of the book.
    :ivar notes: Notes of the user.
    :ivar images: Paths of the images of the page's base version, or None while it has none.
    :ivar created_at: When the page was created.
    :ivar updated_at: When the page was last changed.
    """

    id: PageId
    position: int
    label: str
    kind: PageKind
    origin: PageOrigin
    scan_id: ScanId | None
    slot: int
    included: bool
    notes: str
    images: ImagePathsSchema | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_overview(cls, overview: PageOverview, request: Request) -> Self:
        """Build the schema of a page with the paths of its images.

        :param overview: The page with its position and base version.
        :type overview: PageOverview
        :param request: The request, whose application knows the route that serves the images.
        :type request: Request
        :returns: The page resource, with images once its base version has them cut.
        :rtype: Self
        """
        page, version = overview.page, overview.base_version
        images = None
        if version is not None and version.renditions is not None and version.renditions.ready:
            keys = ProjectKeys(page.project_id)
            images = ImagePathsSchema.of(request, lambda rendition: keys.version_rendition(version, rendition))
        return cls(
            id=page.id,
            position=overview.position,
            label=page.label,
            kind=page.kind,
            origin=page.origin,
            scan_id=page.scan_id,
            slot=page.slot,
            included=page.included,
            notes=page.notes,
            images=images,
            created_at=page.created_at,
            updated_at=page.updated_at,
        )
