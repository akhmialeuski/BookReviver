"""The rule that computes the printed number of every page of a book from its pagination sections.

The sections of the main flow follow each other, and each lasts until the next one starts. A series by kind takes the
pages of its kinds from its first page on, and lasts for a kind until another series that takes the kind starts, so it
never interrupts the main flow: the pages it takes are not counted there. A page kept out of the book takes no number.
A page whose label was written by hand keeps it, and still counts in its section, because a number typed over a page
does not move the pages around it.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from bookreviver.domain.entities import Page, PaginationSection
    from bookreviver.domain.enums import PageKind
    from bookreviver.domain.ids import PageId, PaginationSectionId


class Pagination:
    """The numbering that the sections of a book give its pages, read in one pass over the book."""

    def __init__(self, sections: Iterable[PaginationSection], pages: Sequence[Page]) -> None:
        """Work over the sections and the pages of one book.

        A section whose first page is not among the pages takes no part, since it starts nowhere in the book.

        :param sections: Every section of the book, in any order.
        :type sections: Iterable[PaginationSection]
        :param pages: Every page of the book in book order, the pages kept out of the book too.
        :type pages: Sequence[Page]
        """
        self._pages = pages
        position = {page.id: index for index, page in enumerate(pages)}
        self._starting: dict[int, list[PaginationSection]] = defaultdict(list)
        for section in sorted(sections, key=lambda section: (section.is_series, section.created_at, section.id)):
            if (index := position.get(section.first_page_id)) is not None:
                self._starting[index].append(section)

    def labels(self) -> dict[PageId, str]:
        """Compute the label each page shows.

        :returns: The label of every page by identifier: the one written by hand where there is one, and otherwise the
                  number its section gives, which is empty for a page kept out of the book, a page outside every
                  section and a page of a section that does not count.
        :rtype: dict[PageId, str]
        :raises ValueError: If a section cannot write a number, such as 4000 in Roman numerals.
        """
        next_number: dict[PaginationSectionId, int] = {}
        labels: dict[PageId, str] = {}
        for page, governing, started in self._walk():
            for section in started:
                next_number[section.id] = section.start
            computed = ''
            if page.included and governing is not None and governing.display.counts:
                computed = governing.label(next_number[governing.id])
                next_number[governing.id] += 1
            labels[page.id] = page.label if page.label_manual else computed
        return labels

    def section_ids(self) -> dict[PageId, PaginationSectionId | None]:
        """Find the section that governs each page, by the same walk over the book that numbers it.

        A page kept out of the book takes no part in any section, and neither does a page before the first section of
        the main flow that is not taken by a series. A page of a section that does not count is still governed by it.
        No number is written, so a section that cannot write one does not stop the answer.

        :returns: The identifier of the governing section of every page, or None for a page that has none.
        :rtype: dict[PageId, PaginationSectionId | None]
        """
        return {
            page.id: governing.id if page.included and governing is not None else None
            for page, governing, _ in self._walk()
        }

    def _walk(self) -> Iterator[tuple[Page, PaginationSection | None, list[PaginationSection]]]:
        """Walk the pages in book order and name the section that governs each one.

        :returns: For every page, the page, the section that governs it by position alone, whether or not the page is in
                  the book, and the sections that start at it.
        :rtype: Iterator[tuple[Page, PaginationSection | None, list[PaginationSection]]]
        """
        flow: PaginationSection | None = None
        series: dict[PageKind, PaginationSection] = {}
        for index, page in enumerate(self._pages):
            started = self._starting[index]
            for section in started:
                if section.is_series:
                    series.update(dict.fromkeys(section.kinds, section))
                else:
                    flow = section
            yield page, series.get(page.kind, flow), started
