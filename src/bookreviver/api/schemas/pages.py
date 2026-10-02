"""Schema of a page of the book: its place in the book, its kind and the images of its current version.

A page is identified by its ``PageId`` and its place in the book is its ``position``, counted from zero over the pages
of the project in book order. The fractional order key that keeps the order never leaves the server, so a client cannot
build a page order from it. ``images`` shows the current version of the latest stage of the page that has an image,
which is its base version until a processing stage writes a later one, and is empty for a page that has no image yet: a
placeholder waiting for a scan, or a page whose images are still being cut.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Self

from fastapi import Query
from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from bookreviver.api.pagination import ManifestParams
from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.images import ImagePathsSchema
from bookreviver.api.schemas.types import Dpi, LongText, PageIdList, PageLabel, PagePixels
from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import LabelStyle, NewPageOrigin, PageKind, PageOrigin, Side
from bookreviver.domain.ids import PageId, ScanId, SourceId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPage, PageAnchor, PageNumbering, PageSize
from bookreviver.services.pages import PageService

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.entities import PageOverview

EXACTLY_ONE_ANCHOR: str = 'Give exactly one of before_page_id and after_page_id.'
AT_MOST_ONE_ANCHOR: str = 'Give at most one of before_page_id and after_page_id.'
SIZE_IS_A_PAIR: str = 'Give both width_px and height_px, or neither.'
DPI_NEEDS_SIZE: str = 'A resolution is given with a size.'
ONLY_BLANK_HAS_SIZE: str = 'Only a blank leaf has an image to give a size to.'
# Fields of a page patch that have no empty value, so a null is refused
NOT_CLEARABLE_FIELDS: tuple[str, ...] = ('kind', 'included')


class PageQuery(ManifestParams):
    """The query of the page manifest: the page parameters, and whether to leave out the pages kept out of the book.

    :ivar page: Number of the page of the manifest, from one.
    :ivar size: Number of pages of the book in one page of the manifest.
    :ivar included: Whether to list only the pages that are part of the book, numbered among themselves.
    """

    included: bool = Query(default=False, description='List only the pages that are part of the book')


class OptionalAnchor(RequestModel):
    """A place in the book that a body may name by the page it lies before or after, and may leave out.

    :ivar before_page_id: Page the place lies before, or omitted.
    :ivar after_page_id: Page the place lies after, or omitted.
    """

    before_page_id: PageId | None = None
    after_page_id: PageId | None = None

    @model_validator(mode='after')
    def _not_both_sides(self) -> Self:
        """Check that no more than one side is named.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If both sides are given.
        """
        if self.before_page_id is not None and self.after_page_id is not None:
            raise ValueError(AT_MOST_ONE_ANCHOR)
        return self

    @property
    def optional_anchor(self) -> PageAnchor | None:
        """The place as the domain states it, or None when the body names none."""
        named = {Side.BEFORE: self.before_page_id, Side.AFTER: self.after_page_id}
        return next(
            (PageAnchor(page_id=page_id, side=side) for side, page_id in named.items() if page_id is not None), None
        )


class PageAnchorBody(OptionalAnchor):
    """A place in the book that the body has to name, by the page it lies before or after."""

    @model_validator(mode='after')
    def _one_anchor(self) -> Self:
        """Check that a side is named.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If neither side is given.
        """
        if self.before_page_id is None and self.after_page_id is None:
            raise ValueError(EXACTLY_ONE_ANCHOR)
        return self

    @property
    def anchor(self) -> PageAnchor:
        """The place as the domain states it."""
        if (anchor := self.optional_anchor) is None:
            raise ValueError(EXACTLY_ONE_ANCHOR)
        return anchor


class PageMove(PageAnchorBody):
    """Where to put one page: before or after another page."""


class PagesMove(PageAnchorBody):
    """Where to put a group of pages, which keep their order in the book.

    :ivar page_ids: The pages to move, each at most once.
    """

    page_ids: PageIdList


class PageCreate(OptionalAnchor):
    """A page to add without a scan: a placeholder that waits for one, or a generated blank leaf.

    The place is the page it lies before or after, and the end of the book when neither is given. A blank leaf has the
    median size of the book's pages unless its size is given, in which case both sides are, and the resolution may be.

    :ivar origin: Whether the page is a blank leaf or a placeholder; a page from a scan is made by the split and by
                  binding a scan.
    :ivar kind: Role of the page in the book.
    :ivar label: Printed number of the page, or empty.
    :ivar notes: Notes of the user.
    :ivar width_px: Width of a blank leaf in pixels, given with its height.
    :ivar height_px: Height of a blank leaf in pixels, given with its width.
    :ivar dpi: Resolution of a blank leaf in dots per inch, given with its size.
    """

    origin: NewPageOrigin
    kind: PageKind
    label: PageLabel = ''
    notes: LongText = ''
    width_px: PagePixels | None = None
    height_px: PagePixels | None = None
    dpi: Dpi | None = None

    @model_validator(mode='after')
    def _size_belongs_to_a_blank_leaf(self) -> Self:
        """Check that the size is a pair, that the resolution comes with it, and that only a blank leaf has them.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If only one side is given, a resolution comes without a size, or a placeholder has a size.
        """
        if (self.width_px is None) != (self.height_px is None):
            raise ValueError(SIZE_IS_A_PAIR)
        if self.dpi is not None and self.width_px is None:
            raise ValueError(DPI_NEEDS_SIZE)
        if self.width_px is not None and self.origin is not NewPageOrigin.BLANK:
            raise ValueError(ONLY_BLANK_HAS_SIZE)
        return self

    def to_new_page(self) -> NewPage:
        """Return the page as the domain states it.

        :returns: The new page, with its place and the size of a blank leaf if one was given.
        :rtype: NewPage
        """
        size = None
        if self.width_px is not None and self.height_px is not None:
            size = PageSize(width_px=self.width_px, height_px=self.height_px, dpi=self.dpi)
        return NewPage(
            origin=self.origin,
            kind=self.kind,
            label=self.label,
            notes=self.notes,
            anchor=self.optional_anchor,
            size=size,
        )


# The pages of one batch request: at least one, and no more than the service adds in one transaction
PageCreateList = Annotated[
    list[PageCreate],
    Field(min_length=1, max_length=PageService.MAX_PAGES_PER_BATCH),
]


class ScanAttach(RequestModel):
    """The scan to bind to a placeholder.

    :ivar scan_id: Scan to bind, which must be a scan of the project.
    :ivar take_over: Whether to take the scan from the pages that show it already, such as the page the import made of
                     it, which deletes them.
    """

    scan_id: ScanId
    take_over: bool = False


class PageUpdate(RequestModel):
    """A JSON Merge Patch of a page: an omitted field is kept, and a null label or note is cleared.

    The kind and the inclusion of a page have no empty value, so they may be omitted but not null.

    :ivar label: New printed number, or None to clear it.
    :ivar kind: New role of the page in the book.
    :ivar included: New decision whether the page is part of the book.
    :ivar notes: New notes, or None to clear them.
    """

    label: PageLabel | None = None
    kind: PageKind | SkipJsonSchema[None] = None
    included: bool | SkipJsonSchema[None] = None
    notes: LongText | None = None

    @model_validator(mode='after')
    def _kind_and_inclusion_are_not_cleared(self) -> Self:
        """Refuse a kind or an inclusion that was sent, and sent as null.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If the client sent null for the kind or the inclusion.
        """
        if cleared := [
            name for name in NOT_CLEARABLE_FIELDS if name in self.model_fields_set and getattr(self, name) is None
        ]:
            err_msg = f'The field {cleared[0]} may be omitted, but not null.'
            raise ValueError(err_msg)
        return self

    def to_changes(self) -> PageChanges:
        """Return the fields the client sent as a domain change, a null label or note changed to an empty string.

        :returns: The change replacing exactly the fields present in the request body.
        :rtype: PageChanges
        """
        sent = self.model_fields_set
        return PageChanges(
            label=(self.label or '') if 'label' in sent else None,
            kind=self.kind,
            included=self.included,
            notes=(self.notes or '') if 'notes' in sent else None,
        )


class LabelRange(RequestModel):
    """A range of pages to number, from one page to another in the order of the book.

    :ivar first_page_id: First page of the range.
    :ivar last_page_id: Last page of the range, which may be the first page but not stand before it.
    :ivar style: How the numbers are written; ``none`` erases the labels of the range.
    :ivar start: Number of the first numbered page, from 1, and at most 3999 in a Roman style.
    :ivar bracketed: Whether to enclose the label in square brackets.
    :ivar skip_kinds: Kinds of page that take no number and keep their label, such as plates.
    """

    first_page_id: PageId
    last_page_id: PageId
    style: LabelStyle
    start: Annotated[int, Field(ge=1)] = 1
    bracketed: bool = False
    skip_kinds: list[PageKind] = Field(default_factory=list)

    def to_numbering(self) -> PageNumbering:
        """Return the range as the domain states it.

        :returns: The numbering of the range.
        :rtype: PageNumbering
        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        return PageNumbering(
            first_page_id=self.first_page_id,
            last_page_id=self.last_page_id,
            style=self.style,
            start=self.start,
            bracketed=self.bracketed,
            skip_kinds=frozenset(self.skip_kinds),
        )

    @model_validator(mode='after')
    def _first_number_fits_the_style(self) -> Self:
        """Check that the first number can be written in the style.

        :returns: The range unchanged.
        :rtype: Self
        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)
        return self


class NumberedPageSchema(ResponseModel):
    """A page and the label a numbering would give it, shown before the numbering is saved.

    :ivar page_id: The page.
    :ivar label: The label the page would have, empty for the style ``none``.
    """

    page_id: PageId
    label: str


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
    :ivar images: Paths of the images of the page's current version, or None while it has none.
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
        page, version = overview.page, overview.image_version
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
