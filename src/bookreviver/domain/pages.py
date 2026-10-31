"""The pages of a book: what a page is, where its image comes from, and how a client sees it."""

from typing import TYPE_CHECKING, ClassVar, Final

from attrs import field, frozen, validators

from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.step_settings import PaperFill

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.ids import PageId, PaginationSectionId, ScanId, SourceId
    from bookreviver.domain.sides import Side
    from bookreviver.domain.versions import PageSize, PageVersion


class PageKind(LabeledStrEnum):
    """Role of a page in the printed book."""

    COVER = 'cover', 'Cover'
    BACK_COVER = 'back-cover', 'Back cover'
    ENDPAPER = 'endpaper', 'Endpaper'
    FRONTISPIECE = 'frontispiece', 'Frontispiece'
    TITLE = 'title', 'Title page'
    TEXT = 'text', 'Text page'
    PLATE = 'plate', 'Plate'
    BLANK = 'blank', 'Blank page'
    OTHER = 'other', 'Other'


# The kinds of page that are pictures whatever the program finds in them
PICTURE_KINDS: Final[frozenset[PageKind]] = frozenset({PageKind.PLATE, PageKind.FRONTISPIECE})


class ContentType(LabeledStrEnum):
    """What the image of a page is: text, or a picture in colour or in black and white.

    The program proposes it from the share of the page that pictures cover and from the colour of the pictures, and the
    user may change it. It decides, apart from the role the page has in the book (``PageKind``), which recipe of a stage
    processes the page.
    """

    TEXT = 'text', 'Text'
    COLOR_PICTURE = 'color-picture', 'Colour picture'
    BW_PICTURE = 'bw-picture', 'Black-and-white picture'

    @property
    def is_picture(self) -> bool:
        """Whether the page shows a picture, in colour or not."""
        return self is not ContentType.TEXT

    @classmethod
    def shown_by(cls, kind: PageKind, content_type: ContentType | None, *, by_hand: bool) -> ContentType:
        """Work out what a page shows from its kind and the content type the program found or the user set.

        What the user set decides. Otherwise a plate or a frontispiece is a picture, since the user gave the page that
        role, and any other page is a picture when the program found one on it. A picture whose colour nobody set counts
        as a picture in colour, since a recipe for black and white pictures must not touch a page that may be a plate.

        :param kind: Role of the page in the book.
        :type kind: PageKind
        :param content_type: The content type of the page, or None while it is not detected.
        :type content_type: ContentType | None
        :param by_hand: Whether the user set ``content_type``.
        :type by_hand: bool
        :returns: Text, or the picture in the colour it has.
        :rtype: ContentType
        """
        if content_type is not None and content_type.is_picture:
            return content_type
        return cls.COLOR_PICTURE if kind in PICTURE_KINDS and not by_hand else cls.TEXT


class ContentSource(LabeledStrEnum):
    """Where the content type of a page comes from, which tells the user how far to trust it."""

    DETECTED = 'detected', 'Found by the program'
    HAND = 'hand', 'Set by hand'
    KIND = 'kind', 'Given by the kind of the page'


class RecipeKind(LabeledStrEnum):
    """The kind of page a recipe processes: every stage that has recipes has one for each kind.

    A page is processed by the recipe of its kind, which is a blank page for a page of the blank kind and otherwise what
    the page shows (``ContentType``). Changing the kind of a page moves the page to another recipe.
    """

    TEXT = 'text', 'Text'
    COLOR_PICTURE = 'color-picture', 'Colour picture'
    BW_PICTURE = 'bw-picture', 'Black-and-white picture'
    BLANK = 'blank', 'Blank page'

    @classmethod
    def of(cls, kind: PageKind, content: ContentType) -> RecipeKind:
        """Give the kind of a page.

        :param kind: Role of the page in the book.
        :type kind: PageKind
        :param content: What the page shows, as ``ContentType.shown_by`` works it out.
        :type content: ContentType
        :returns: The blank kind for a blank page, and otherwise the kind of what the page shows.
        :rtype: RecipeKind
        """
        return cls.BLANK if kind is PageKind.BLANK else cls(content.value)


class PageOrigin(LabeledStrEnum):
    """Where the image of a page comes from."""

    SCAN = 'scan', 'Copy of a part of a scan'
    BLANK = 'blank', 'Generated blank leaf'
    PLACEHOLDER = 'placeholder', 'Placeholder waiting for a scan'


class BlankFill(LabeledStrEnum):
    """What the image of a page of kind blank is: the scan the page was cut from, or a leaf made in its place.

    A leaf is a page the program draws, so the steps of the stages after the page order pass it unchanged. The scan of
    the page is kept whatever the choice, so the choice can be undone.
    """

    SCAN = 'scan', 'Keep the scan'
    WHITE = 'white', 'White leaf'
    PAPER = 'paper', 'Paper of the book'

    @property
    def paper_fill(self) -> PaperFill:
        """The fill ``pages.blank`` draws a leaf of this choice with.

        :raises ValueError: If the choice is the scan, which no leaf is drawn for.
        """
        if self is BlankFill.SCAN:
            err_msg = 'A page that keeps its scan has no leaf to fill.'
            raise ValueError(err_msg)
        return PaperFill(self.value)


class PageFilter(LabeledStrEnum):
    """Which pages the strip and the grid of a stage list."""

    ALL = 'all', 'All pages'
    CHECK = 'check', 'Pages to check'
    BAD = 'bad', 'Pages whose result is marked bad'
    LEFT_OUT = 'left-out', 'Pages left out of the book'
    WIDE = 'wide', 'Pages cut from a wide scan'


class NewPageOrigin(LabeledStrEnum):
    """Where the image of a page the user adds comes from.

    Only a blank leaf and a placeholder are added by hand, since a page cut from a scan is made by the page split and
    by binding a scan to a placeholder.
    """

    BLANK = 'blank', 'Generated blank leaf'
    PLACEHOLDER = 'placeholder', 'Placeholder waiting for a scan'

    @property
    def page_origin(self) -> PageOrigin:
        """The origin of the page this one makes."""
        return PageOrigin(self.value)


@frozen(kw_only=True)
class PageAnchor:
    """A place in the book named by a page and the side of it, where moved or new pages are put.

    :ivar page_id: Page the place is next to.
    :ivar side: Whether the place lies before or after that page.
    """

    page_id: PageId
    side: Side


@frozen(kw_only=True)
class NewPage:
    """A page the user adds to the book without a scan: a blank leaf or a placeholder.

    :ivar origin: Whether the page is a generated blank leaf or a placeholder that waits for a scan.
    :ivar kind: Role of the page in the book.
    :ivar label: Printed number of the page, or empty.
    :ivar notes: Notes of the user.
    :ivar anchor: Place the page is put at, or None for the end of the book.
    :ivar size: Size of a blank leaf, or None for the median size of the book's pages; never given for a placeholder.
    """

    origin: NewPageOrigin
    kind: PageKind
    label: str = ''
    notes: str = ''
    anchor: PageAnchor | None = None
    size: PageSize | None = None

    def __attrs_post_init__(self) -> None:
        """Check that only a blank leaf has a size.

        :raises ValueError: If a placeholder is given a size, since it has no image.
        """
        if self.size is not None and self.origin is not NewPageOrigin.BLANK:
            err_msg = f'A {self.origin.label.lower()} has no image, so it has no size.'
            raise ValueError(err_msg)


@frozen(kw_only=True)
class Page:
    """A page of the book in book order, which stands on its own once created.

    A page is cut from a scan by the page split, or added by the user in the page order stage as a blank leaf or a
    placeholder. It keeps its own copy of its image in its base version, so nothing about it depends on its source,
    and its identity survives reordering, splitting again and re-running stages. Its position is the order of its key
    among the keys of the book's pages, and the position number shown to the user is computed when it is read.

    :ivar id: Identifier of the page, which names its storage directory.
    :ivar order_key: Fractional index string whose byte order is the order of the pages in the book.
    :ivar manual_label: Printed number written by hand or taken from the scan, such as ``xii``, which the page shows in
                        place of the number its pagination section gives, or None for the number of the section. The
                        number of a section is never stored, since every page added, moved or removed changes it.
    :ivar kind: Role of the page in the book.
    :ivar content_type: What the image of the page shows as the program found it, or as the user set it when
                        ``content_by_hand`` is set, or None while the content is not detected, which leaves it to the
                        kind of the page.
    :ivar content_by_hand: Whether the user set the content type, which makes it an exception that the detection of the
                           content never changes.
    :ivar origin: Where the image of the page comes from.
    :ivar scan_id: Scan the page was cut from, or None for a blank leaf, a placeholder, or a page whose source was
                   deleted.
    :ivar slot: Part of the scan the page shows: ``0`` the whole scan, ``1`` and ``2`` the halves of a spread, higher
                for a fold-out.
    :ivar blank_fill: What the image of a page of kind blank is: its scan, or a leaf drawn in its place. The scan of the
                      page stays linked whatever the choice, so choosing the scan again brings it back.
    :ivar included: Whether the page is part of the book; off for a colour chart or a duplicate.
    :ivar notes: Notes of the user.
    :ivar group_label: Label of the group the user put the page in by hand, or empty for no group.
    :ivar created_at: When the page was created.
    :ivar updated_at: When the page was last changed.
    """

    WHOLE_SCAN: ClassVar[int] = 0
    LEFT_HALF: ClassVar[int] = 1
    RIGHT_HALF: ClassVar[int] = 2

    id: PageId
    order_key: str = field(validator=validators.min_len(1))
    manual_label: str | None = field(default=None, validator=validators.optional(validators.min_len(1)))
    kind: PageKind = PageKind.TEXT
    content_type: ContentType | None = None
    content_by_hand: bool = False
    origin: PageOrigin
    scan_id: ScanId | None = None
    slot: int = field(default=WHOLE_SCAN, validator=validators.ge(WHOLE_SCAN))
    blank_fill: BlankFill = BlankFill.SCAN
    included: bool = True
    notes: str = ''
    group_label: str = ''
    created_at: datetime
    updated_at: datetime

    def __attrs_post_init__(self) -> None:
        """Check the scan of the page, its leaf and its content type.

        Only a page cut from a scan names a scan, only a blank one of them has a leaf, and a content type said to be set
        by hand has to be given.

        :raises ValueError: If a blank leaf or a placeholder names a scan, a leaf stands in place of the scan of a page
                            that is not a blank page cut from a scan, or the content type is said to be set by hand and
                            is none.
        """
        if self.scan_id is not None and self.origin is not PageOrigin.SCAN:
            err_msg = f'A page of origin {self.origin} has no scan, but names scan {self.scan_id}.'
            raise ValueError(err_msg)
        if self.content_by_hand and self.content_type is None:
            err_msg = 'A content type set by hand has to be given.'
            raise ValueError(err_msg)
        cut_blank = self.origin is PageOrigin.SCAN and self.kind is PageKind.BLANK
        if self.blank_fill is not BlankFill.SCAN and not cut_blank:
            err_msg = (
                f'A leaf replaces the scan of a blank page cut from a scan, not of a {self.kind} page of {self.origin}.'
            )
            raise ValueError(err_msg)

    @property
    def is_leaf(self) -> bool:
        """Whether the image of the page is drawn by the program, so no step of a stage has anything to find in it."""
        return self.origin is PageOrigin.BLANK or self.blank_fill is not BlankFill.SCAN

    @property
    def content(self) -> ContentType:
        """What the page shows: the content type the user set, else the one found, else the one its kind gives."""
        return ContentType.shown_by(self.kind, self.content_type, by_hand=self.content_by_hand)

    @property
    def recipe_kind(self) -> RecipeKind:
        """The kind of the page, by which every stage that has recipes chooses the recipe that processes the page."""
        return RecipeKind.of(self.kind, self.content)

    @property
    def content_source(self) -> ContentSource:
        """Where what the page shows comes from: the user, the detection, or the kind of the page."""
        if self.content_by_hand:
            return ContentSource.HAND
        if self.kind not in PICTURE_KINDS and self.content_type is not None:
            return ContentSource.DETECTED
        return ContentSource.KIND


@frozen(kw_only=True)
class PageOverview:
    """A page of the book with what a client shows of it: its place in the book and the image it starts from.

    :ivar page: The page of the book.
    :ivar position: Place of the page in the book from zero, counted over every page, excluded or not.
    :ivar image_version: The version whose renditions show the page: the current version of the latest stage that has
                         an image, which is the base version until a later stage makes one, or None for a page that
                         has no image yet, such as a placeholder.
    :ivar source_id: Source holding the page's scan, which selects every page of one source, or None for a page
                     without a scan.
    :ivar section_id: The pagination section that governs the page, or None for a page kept out of the book, a page
                      before the first section and a book without sections.
    :ivar label: Printed number the page shows: its manual label, or the number its section gives, or empty for an
                 unnumbered page.
    """

    page: Page
    position: int = field(validator=validators.ge(0))
    image_version: PageVersion | None = None
    source_id: SourceId | None = None
    section_id: PaginationSectionId | None = None
    label: str = ''
