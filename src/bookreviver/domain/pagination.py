"""The rule that computes the printed number of every page of a book from its pagination sections.

The sections of the main flow follow each other, and each lasts until the next one starts. A series by kind takes the
pages of its kinds from its first page on, and lasts for a kind until another series that takes the kind starts, so it
never interrupts the main flow: the pages it takes are not counted there. A page kept out of the book takes no number.
A page with a manual label shows it, and still counts in its section, because a number typed over a page does not move
the pages around it. The numbers are computed whenever the pages are read and never stored.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.domain.page_label_rules import BookLabeling, LabelStyle, PageLabelRule, PrintedNumber

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from datetime import datetime

    from bookreviver.domain.ids import PageId, PaginationSectionId
    from bookreviver.domain.pages import Page, PageKind
from attrs import field, validators

from bookreviver.domain.labels import LabeledStrEnum


class NumberDisplay(LabeledStrEnum):
    """How the pages of a pagination section show their numbers, and whether they take part in the count.

    A page that is counted but not printed is shown with its number in square brackets, as a bibliographer writes a
    number the book implies and does not print, such as ``[iii]``.
    """

    NOT_COUNTED = 'not-counted', 'Not counted'
    COUNTED = 'counted', 'Counted, not printed'
    PRINTED = 'printed', 'Printed'

    @property
    def counts(self) -> bool:
        """Whether a page of the section takes a number and so moves the count on."""
        return self is not NumberDisplay.NOT_COUNTED


@frozen(kw_only=True)
class PageNumbering:
    """How to number a range of pages, which makes a pagination section of the book and is not stored itself.

    The range runs from one page to another in the order of the book. Numbering it makes a section of the main flow
    that starts at the first page, a series of the kinds in ``skip_kinds`` that does not count, and, when the book goes
    on after the last page, a section after the range that does not count either. Pages kept out of the book, and pages
    of the skipped kinds, take no number and keep their label, since the plates of an old book are usually not counted.

    :ivar first_page_id: First page of the range.
    :ivar last_page_id: Last page of the range, which may be the first page but not stand before it.
    :ivar style: How the numbers are written; ``none`` erases the labels of the range.
    :ivar start: Number of the first numbered page, from 1.
    :ivar bracketed: Whether the pages are counted and not printed, so the label is enclosed in square brackets, as a
                     bibliographer marks a number that is not printed in the book.
    :ivar skip_kinds: Kinds of page that are not numbered.
    """

    first_page_id: PageId
    last_page_id: PageId
    style: LabelStyle
    start: int = field(default=1, validator=validators.ge(1))
    bracketed: bool = False
    skip_kinds: frozenset[PageKind] = frozenset()

    def __attrs_post_init__(self) -> None:
        """Check that the first number can be written in the style.

        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)


@frozen(kw_only=True)
class PaginationSectionDraft:
    """What the user states of a pagination section, to make a section or to replace the rule of one.

    :ivar first_page_id: The page the section starts at.
    :ivar name: Name the user sees.
    :ivar style: How the numbers are written.
    :ivar start: Number of the first counted page, from 1.
    :ivar prefix: Text written before every number.
    :ivar display: Whether the pages count, and whether their numbers are printed or only implied.
    :ivar kinds: Kinds of page a series by kind takes; empty for a section of the main flow.
    """

    first_page_id: PageId
    name: str = ''
    style: LabelStyle
    start: int = field(default=1, validator=validators.ge(1))
    prefix: str = ''
    display: NumberDisplay = NumberDisplay.PRINTED
    kinds: frozenset[PageKind] = frozenset()

    def __attrs_post_init__(self) -> None:
        """Check that the first number can be written in the style.

        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)


@frozen(kw_only=True)
class NumberedPage:
    """A page and the label a numbering would give it, which a preview shows before anything is written.

    :ivar page_id: The page.
    :ivar label: The label the page would have, empty for the numbering style ``none``.
    """

    page_id: PageId
    label: str


@frozen(kw_only=True)
class PaginationSection(PaginationSectionDraft):
    """A run of the pages of a book that is numbered by one rule, such as the Roman numbers of a preface.

    A section starts at its first page and lasts until the next section of its own flow starts. A section that names
    no kinds belongs to the main flow of the book, and a section with kinds is a series by kind: it takes the pages of
    those kinds from its first page on, wherever they stand, and keeps its own count, so the plates of a book get
    ``Plate I`` and ``Plate II`` without disturbing the numbers of the text.

    The rule of a section is the :class:`PaginationSectionDraft` the user stated, which the section extends with its
    identity. The section follows its first page when the page is moved.

    :ivar id: Identifier of the section.
    :ivar created_at: When the section was created.
    :ivar updated_at: When the section was last changed.
    """

    id: PaginationSectionId
    created_at: datetime
    updated_at: datetime

    @property
    def is_series(self) -> bool:
        """Whether the section is a series by kind, which does not interrupt the main flow."""
        return bool(self.kinds)

    def label(self, number: int) -> str:
        """Write the label of the page that takes ``number`` in the section.

        :param number: Number of the page, counted from ``start``.
        :type number: int
        :returns: The number in the style of the section after its prefix, in square brackets when the pages are counted
                  and not printed, and empty when the pages are not counted or the style writes no number.
        :rtype: str
        :raises ValueError: If the style cannot write the number, such as 4000 in Roman numerals.
        """
        text = self.style.write(number)
        if not text or not self.display.counts:
            return ''
        label = f'{self.prefix}{text}'
        return f'[{label}]' if self.display is NumberDisplay.COUNTED else label

    def clashes_with(self, other: PaginationSection) -> bool:
        """Tell whether the two sections start at one page and would take the same pages, so the later one wins.

        :param other: Another section of the book.
        :type other: PaginationSection
        :returns: True when both start at the same page and are both of the main flow, or are series that share a kind.
        :rtype: bool
        """
        if self.id == other.id or self.first_page_id != other.first_page_id:
            return False
        if self.is_series and other.is_series:
            return bool(self.kinds & other.kinds)
        return self.is_series == other.is_series


@frozen(kw_only=True)
class Numbering:
    """What the sections of a book give each of its pages: the label it shows and the section that governs it.

    :ivar labels: The label of every page by identifier, as ``Pagination.labels`` gives it.
    :ivar section_ids: The governing section of every page by identifier, as ``Pagination.section_ids`` gives it.
    """

    labels: Mapping[PageId, str]
    section_ids: Mapping[PageId, PaginationSectionId | None]


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
        labels: dict[PageId, str] = {}
        for page, section, number in self._numbered():
            computed = section.label(number) if section is not None else ''
            labels[page.id] = computed if page.manual_label is None else page.manual_label
        return labels

    def labeling(self) -> BookLabeling:
        """Write the numbering of the pages kept in the book as page label rules and printed numbers.

        The pages kept out of the book are not in the output, so the positions count the pages that stay. A run of
        pages that follow one another in one section is one rule, and a page with a label written by hand is a rule of
        its own that writes the label as a prefix, after which its section goes on with a rule that starts at the
        number it has reached. A page that takes no number, such as one of a section that does not count, has the empty
        label. A page of a section that counts without printing is numbered by its rule and prints no number.

        :returns: The rules, which start at the first page, and the numbers the pages print.
        :rtype: BookLabeling
        """
        rules: list[PageLabelRule] = []
        printed: list[PrintedNumber] = []
        index = -1
        for page, section, number in self._numbered():
            if not page.included:
                continue
            index += 1
            style, prefix, value = LabelStyle.NONE, '', 1
            if page.manual_label is not None:
                prefix = page.manual_label
                printed.append(PrintedNumber(index=index, label=page.manual_label))
            elif section is not None and section.style is not LabelStyle.NONE:
                style, prefix, value = section.style, section.prefix, number
                if section.display is NumberDisplay.PRINTED:
                    printed.append(PrintedNumber(index=index, label=section.label(number)))
            if not rules or not rules[-1].continues(index, style=style, prefix=prefix, number=value):
                rules.append(PageLabelRule(first_index=index, style=style, prefix=prefix, start=value))
        return BookLabeling(rules=rules, printed=printed)

    def numbering(self) -> Numbering:
        """Compute the label of every page together with the section that governs it.

        :returns: The labels and the governing sections of the pages.
        :rtype: Numbering
        :raises ValueError: If a section cannot write a number, such as 4000 in Roman numerals.
        """
        return Numbering(labels=self.labels(), section_ids=self.section_ids())

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

    def _numbered(self) -> Iterator[tuple[Page, PaginationSection | None, int]]:
        """Walk the book once, giving every page the section that numbers it and the number it takes there.

        :returns: Iterator over the pages in book order, each with its section and number, or with no section and the
                  number 0 for a page that takes none: one kept out of the book, one outside every section, and one of
                  a section that does not count.
        :rtype: Iterator[tuple[Page, PaginationSection | None, int]]
        """
        next_number: dict[PaginationSectionId, int] = {}
        for page, governing, started in self._walk():
            for section in started:
                next_number[section.id] = section.start
            if page.included and governing is not None and governing.display.counts:
                yield page, governing, next_number[governing.id]
                next_number[governing.id] += 1
            else:
                yield page, None, 0
