"""Schema of a page of the book: its place in the book, its kind and the images of its base version.

A page is identified by its ``PageId`` and its place in the book is its ``position``, counted from zero over the pages
of the project in book order. The fractional order key that keeps the order never leaves the server, so a client cannot
build a page order from it. ``images`` shows the base version of the page, which is the only version recorded until
the processing stages write their own, and is empty for a page that has no image yet: a placeholder waiting for a scan,
or a page whose images are still being cut.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Self

from fastapi import Query
from pydantic import model_validator

from bookreviver.api.pagination import ManifestParams
from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.images import ImagePathsSchema
from bookreviver.api.schemas.types import PageIdList
from bookreviver.domain.enums import PageKind, PageOrigin, Side
from bookreviver.domain.ids import PageId, ScanId, SourceId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageAnchor

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.entities import PageOverview

EXACTLY_ONE_ANCHOR: str = 'Give exactly one of before_page_id and after_page_id.'


class PageQuery(ManifestParams):
    """The query of the page manifest: the page parameters, and whether to leave out the pages kept out of the book.

    :ivar page: Number of the page of the manifest, from one.
    :ivar size: Number of pages of the book in one page of the manifest.
    :ivar included: Whether to list only the pages that are part of the book, numbered among themselves.
    """

    included: bool = Query(default=False, description='List only the pages that are part of the book')


class PageAnchorBody(RequestModel):
    """A place in the book, named by the page it lies before or after.

    :ivar before_page_id: Page the place lies before, or omitted.
    :ivar after_page_id: Page the place lies after, or omitted.
    """

    before_page_id: PageId | None = None
    after_page_id: PageId | None = None

    @model_validator(mode='after')
    def _one_anchor(self) -> Self:
        """Check that exactly one side is named.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If both sides or neither are given.
        """
        if (self.before_page_id is None) == (self.after_page_id is None):
            raise ValueError(EXACTLY_ONE_ANCHOR)
        return self

    @property
    def anchor(self) -> PageAnchor:
        """The place as the domain states it."""
        named = {Side.BEFORE: self.before_page_id, Side.AFTER: self.after_page_id}
        side, page_id = next((side, page_id) for side, page_id in named.items() if page_id is not None)
        return PageAnchor(page_id=page_id, side=side)


class PageMove(PageAnchorBody):
    """Where to put one page: before or after another page."""


class PagesMove(PageAnchorBody):
    """Where to put a group of pages, which keep their order in the book.

    :ivar page_ids: The pages to move, each at most once.
    """

    page_ids: PageIdList


class PageSchema(ResponseModel):
    """A page of a book.

    :ivar id: Identifier of the page, which stays the same when the page is moved.
    :ivar position: Place of the page in the book from zero, counted over every page, excluded ones included, except
                    in a manifest listing only the included pages, which numbers those.
    :ivar label: Printed number, such as ``xii`` or ``12``, or empty for an unnumbered page.
    :ivar kind: Role of the page in the book.
    :ivar origin: Where the image of the page comes from.
    :ivar scan_id: Scan the page was cut from, or None for a blank leaf, a placeholder, or a page whose source was
                   deleted.
    :ivar source_id: Source holding the page's scan, by which a client selects every page of one source, or None.
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
    source_id: SourceId | None
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
            images = ImagePathsSchema.of(
                request, lambda rendition: keys.version_rendition(version, rendition), full=version.renditions.full
            )
        return cls(
            id=page.id,
            position=overview.position,
            label=page.label,
            kind=page.kind,
            origin=page.origin,
            scan_id=page.scan_id,
            source_id=overview.source_id,
            slot=page.slot,
            included=page.included,
            notes=page.notes,
            images=images,
            created_at=page.created_at,
            updated_at=page.updated_at,
        )
