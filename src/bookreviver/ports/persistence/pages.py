"""The repository ports of the pages of a book, in the order of the book, and of its pagination sections."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.ids import (
    PageId,
    PaginationSectionId,
)
from bookreviver.domain.pages import Page
from bookreviver.domain.pagination import PaginationSection
from bookreviver.ports.persistence.base import Repository

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bookreviver.domain.ids import (
        ScanId,
        SourceId,
    )
    from bookreviver.domain.pages import BookOrder, RecipeKind
    from bookreviver.domain.sides import Side
    from bookreviver.domain.slices import Slice, SliceRequest


class PageRepository(Repository[Page, PageId]):
    """Pages of the book, addressed by their identifier and ordered by their order key.

    An order key is unique, and a part of a scan, the pair of a scan and a slot, belongs to one page at most. A page
    keeps its row when its scan is deleted, and loses only the reference to it. Deleting a page removes its versions.
    """

    @abstractmethod
    def list_window(self, request: SliceRequest, *, included_only: bool = False) -> Slice[Page]:
        """Return the pages of a book in book order, the byte order of their order keys.

        The position of a page in the book is the offset of the window plus its index in the window.

        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book, which then take no part in the
                              offset either.
        :type included_only: bool
        :returns: The pages of the window and the number of all the book's pages, or of its included pages when
                  ``included_only`` is set.
        :rtype: Slice[Page]
        """

    @abstractmethod
    def list_all(self) -> Sequence[Page]:
        """Return every page of a book in book order, the placeholders and the pages kept out of the book included.

        :returns: The pages of the book.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    def list_by_ids(self, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a book in book order.

        :param page_ids: Identifiers of the pages to read, repeats counting once.
        :type page_ids: Collection[PageId]
        :returns: The pages, in the byte order of their order keys.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page.
        """

    @abstractmethod
    def list_for_source(self, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to a source, in book order.

        Pages without a scan, such as placeholders and blank leaves, belong to no source and are never listed.

        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source, by order key.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    def neighbour_key(self, key: str, side: Side, *, excluding: Collection[PageId] = ()) -> str | None:
        """Return the order key of the page next to ``key`` on one side, leaving some pages out of the count.

        A group of pages that is about to move is left out, so the key found is the one of the page the group will
        stand next to once it has left its old place.

        :param key: Order key the neighbour is looked up from, usually the key of an anchor page.
        :type key: str
        :param side: Whether to look for the nearest key before or after ``key``.
        :type side: Side
        :param excluding: Pages that do not count as neighbours.
        :type excluding: Collection[PageId]
        :returns: The nearest key on that side, or None when no page lies there, which is the start or the end of the
                  book.
        :rtype: str | None
        """

    @abstractmethod
    def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages in the one transaction, all of them or, on an error, none.

        After an error the change that holds the write rolls back when its work raises the exception, as a database
        requires.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes an order key, or a part of a scan, that another page
                               of the book has.
        """

    @abstractmethod
    def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan, by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan, the whole scan or its parts in slot order.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    def count_before(self, page: Page) -> int:
        """Return how many pages of the page's book come before it in book order, which is its position.

        Every page counts, whether it is included in the book or not, as in the windows ``list_window`` returns.

        :param page: Stored page of the book.
        :type page: Page
        :returns: The number of the book's pages whose order key is smaller than the page's, from zero.
        :rtype: int
        """

    @abstractmethod
    def order(self) -> BookOrder:
        """Return the order key of every page of the book in book order, in one read of the keys alone.

        Every page counts, whether it is included in the book or not, as in ``count_before``.

        :returns: The order of the pages of the book, empty for a book without pages.
        :rtype: BookOrder
        """

    @abstractmethod
    def last_order_key(self) -> str | None:
        """Return the order key of the last page of a book, after which new pages are appended.

        :returns: The greatest order key of the book's pages, or None for a book without pages.
        :rtype: str | None
        """

    @abstractmethod
    def kind_tally(self) -> Mapping[RecipeKind, int]:
        """Count the pages with an image of each kind of a book, in one grouped query.

        The kind of a page is ``Page.recipe_kind``. A placeholder has no image and is never counted.

        :returns: The number of pages of each kind that has any page.
        :rtype: Mapping[RecipeKind, int]
        """


class PaginationSectionRepository(Repository[PaginationSection, PaginationSectionId]):
    """The pagination sections of the books, which a page of the book is numbered from.

    A section names the page it starts at. Deleting that page, or the book, removes the section with it, so a use
    case that deletes pages hands the section to the next page first.
    """

    @abstractmethod
    def list_all(self) -> Sequence[PaginationSection]:
        """Return the sections of a book in the order they were made, ties by identifier.

        The sections are not in book order, since the order of the book is the order of the pages they start at.

        :returns: Every section of the book.
        :rtype: Sequence[PaginationSection]
        """
