"""Tests for the pagination sections of the domain and the labels they give the pages of a book."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.enums import LabelStyle, NumberDisplay, PageKind
from bookreviver.domain.page_label_rules import PageLabelRule, PrintedNumber
from bookreviver.domain.pagination import Pagination
from tests.helpers.builders import make_page, make_project, make_section, new_account_id

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PaginationSection
    from bookreviver.domain.ids import PaginationSectionId

PLATES: frozenset[PageKind] = frozenset({PageKind.PLATE, PageKind.FRONTISPIECE})
PLATE_PREFIX: str = 'Plate '
FIRST_PLATE: str = 'Plate 1'
THIRD_ROMAN_PLATE: str = 'Plate III'
BRACKETED_THIRD: str = '[iii]'
ROMAN_LIMIT: int = 3999
HAND_WRITTEN: str = '12a'


def _book(*kinds: PageKind) -> list[Page]:
    """Build the pages of a book of the given kinds, in book order.

    :param kinds: Kind of each page, in book order.
    :type kinds: PageKind
    :returns: Pages of one project, included, without labels.
    :rtype: list[Page]
    """
    project = make_project(owner_id=new_account_id())
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(kinds))
    return [make_page(project_id=project.id, order_key=key, kind=kind) for key, kind in zip(keys, kinds, strict=True)]


def _labels(pages: list[Page], *sections: PaginationSection) -> list[str]:
    """Compute the labels the sections give the pages, in book order.

    :param pages: Pages of the book in book order.
    :type pages: list[Page]
    :param sections: Sections of the book.
    :type sections: PaginationSection
    :returns: The label of each page.
    :rtype: list[str]
    """
    labels = Pagination(sections, pages).labels()
    return [labels[page.id] for page in pages]


class TestPaginationSection:
    """Tests for PaginationSection."""

    @pytest.mark.parametrize(
        ('style', 'prefix', 'display', 'expected'),
        [
            (LabelStyle.ARABIC, '', NumberDisplay.PRINTED, '3'),
            (LabelStyle.ROMAN_LOWER, '', NumberDisplay.PRINTED, 'iii'),
            (LabelStyle.ROMAN_LOWER, '', NumberDisplay.COUNTED, BRACKETED_THIRD),
            (LabelStyle.ROMAN_UPPER, PLATE_PREFIX, NumberDisplay.PRINTED, THIRD_ROMAN_PLATE),
            (LabelStyle.ARABIC, PLATE_PREFIX, NumberDisplay.COUNTED, '[Plate 3]'),
            (LabelStyle.ALPHA_LOWER, '', NumberDisplay.PRINTED, 'c'),
            (LabelStyle.ARABIC, '', NumberDisplay.NOT_COUNTED, ''),
            (LabelStyle.NONE, PLATE_PREFIX, NumberDisplay.PRINTED, ''),
            (LabelStyle.NONE, '', NumberDisplay.COUNTED, ''),
        ],
    )
    def test_label_writes_the_prefix_the_number_and_the_brackets(
        self, style: LabelStyle, prefix: str, display: NumberDisplay, expected: str
    ) -> None:
        """Verify the label of the number 3 for each style, prefix and display, and that nothing is written for no label.

        :param style: How the numbers are written.
        :type style: LabelStyle
        :param prefix: Text before the number.
        :type prefix: str
        :param display: Whether the pages count and whether their numbers are printed.
        :type display: NumberDisplay
        :param expected: The label of the number 3.
        :type expected: str
        """
        [page] = _book(PageKind.TEXT)
        section = evolve(make_section(page=page, style=style, display=display), prefix=prefix)

        assert section.label(3) == expected

    def test_a_roman_section_cannot_start_where_the_numerals_end(self) -> None:
        """Verify the first number must be writable in the style."""
        [page] = _book(PageKind.TEXT)

        with pytest.raises(ValueError, match=str(ROMAN_LIMIT + 1)):
            evolve(make_section(page=page, style=LabelStyle.ROMAN_LOWER), start=ROMAN_LIMIT + 1)

    def test_a_series_is_a_section_with_kinds(self) -> None:
        """Verify only a section that names kinds is a series by kind."""
        [page] = _book(PageKind.TEXT)

        expect(make_section(page=page).is_series is False)
        expect(make_section(page=page, kinds=PLATES).is_series is True)
        assert_expectations()

    @pytest.mark.parametrize(
        ('kinds', 'other_kinds', 'same_page', 'clash'),
        [
            (frozenset(), frozenset(), True, True),
            (frozenset(), frozenset(), False, False),
            (frozenset(), PLATES, True, False),
            (PLATES, frozenset({PageKind.PLATE}), True, True),
            (PLATES, frozenset({PageKind.COVER}), True, False),
            (PLATES, frozenset({PageKind.PLATE}), False, False),
        ],
    )
    def test_two_sections_clash_when_they_start_at_one_page_and_take_the_same_pages(
        self, kinds: frozenset[PageKind], other_kinds: frozenset[PageKind], *, same_page: bool, clash: bool
    ) -> None:
        """Verify main-flow sections clash with each other, and series only when they share a kind.

        :param kinds: Kinds of the first section.
        :type kinds: frozenset[PageKind]
        :param other_kinds: Kinds of the second section.
        :type other_kinds: frozenset[PageKind]
        :param same_page: Whether both sections start at the same page.
        :type same_page: bool
        :param clash: Whether the sections clash.
        :type clash: bool
        """
        first, second = _book(PageKind.TEXT, PageKind.TEXT)
        one = make_section(page=first, kinds=kinds)
        other = make_section(page=first if same_page else second, kinds=other_kinds)

        assert one.clashes_with(other) is clash


class TestPagination:
    """Tests for Pagination.labels()."""

    def test_roman_front_matter_is_followed_by_arabic_text_from_one(self) -> None:
        """Verify a book numbers its preface in Roman numerals and its text in Arabic ones, from the page each starts at."""
        pages = _book(*[PageKind.TEXT] * 5)
        preface = make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER)
        body = make_section(page=pages[2], minutes=1)

        assert _labels(pages, preface, body) == ['i', 'ii', '1', '2', '3']

    def test_a_section_lasts_until_the_next_one_whatever_the_order_they_are_given_in(self) -> None:
        """Verify the sections are told apart by the pages they start at, and not by their order in the call."""
        pages = _book(*[PageKind.TEXT] * 4)
        preface = make_section(page=pages[0], style=LabelStyle.ROMAN_UPPER)
        body = evolve(make_section(page=pages[1]), start=10)

        assert _labels(pages, body, preface) == ['I', '10', '11', '12']

    def test_a_page_counted_and_not_printed_is_shown_in_square_brackets(self) -> None:
        """Verify the pages of the front matter that count without a printed number show it in brackets."""
        pages = _book(*[PageKind.TEXT] * 4)
        front = make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER, display=NumberDisplay.COUNTED)
        body = make_section(page=pages[3])

        assert _labels(pages, front, body) == ['[i]', '[ii]', BRACKETED_THIRD, '1']

    def test_a_page_that_is_not_counted_takes_no_number_and_leaves_the_count_alone(self) -> None:
        """Verify a cover section leaves its pages unnumbered and the next section starts from its own first number."""
        pages = _book(PageKind.COVER, PageKind.ENDPAPER, PageKind.TEXT, PageKind.TEXT)
        covers = make_section(page=pages[0], display=NumberDisplay.NOT_COUNTED)
        body = make_section(page=pages[2], minutes=1)

        assert _labels(pages, covers, body) == ['', '', '1', '2']

    def test_a_page_before_every_section_takes_no_number(self) -> None:
        """Verify pages that no section has started at yet are unnumbered."""
        pages = _book(*[PageKind.TEXT] * 3)

        assert _labels(pages, make_section(page=pages[1])) == ['', '1', '2']

    def test_a_book_without_sections_has_no_computed_numbers(self) -> None:
        """Verify the labels of a book that has no section are empty for every page that has none by hand."""
        pages = _book(*[PageKind.TEXT] * 2)

        assert _labels(pages) == ['', '']

    def test_plates_get_their_own_series_without_breaking_the_text_count(self) -> None:
        """Verify plates number Plate I, Plate II across the book while the text counts on, skipping them."""
        pages = _book(
            PageKind.FRONTISPIECE, PageKind.TEXT, PageKind.TEXT, PageKind.PLATE, PageKind.TEXT, PageKind.PLATE
        )
        text = make_section(page=pages[0])
        plates = evolve(make_section(page=pages[0], style=LabelStyle.ROMAN_UPPER, kinds=PLATES), prefix=PLATE_PREFIX)

        assert _labels(pages, text, plates) == ['Plate I', '1', '2', 'Plate II', '3', THIRD_ROMAN_PLATE]

    def test_a_series_does_not_start_before_its_first_page(self) -> None:
        """Verify a plate that stands before the series follows the main flow."""
        pages = _book(PageKind.PLATE, PageKind.TEXT, PageKind.PLATE)
        text = make_section(page=pages[0])
        plates = evolve(make_section(page=pages[1], kinds=PLATES), prefix=PLATE_PREFIX)

        assert _labels(pages, text, plates) == ['1', '2', FIRST_PLATE]

    def test_a_later_series_of_the_same_kind_takes_over_with_its_own_count(self) -> None:
        """Verify a second series of the plates ends the first one and counts from its own first number."""
        pages = _book(PageKind.PLATE, PageKind.PLATE, PageKind.TEXT, PageKind.PLATE)
        first = evolve(make_section(page=pages[0], kinds=PLATES), prefix=PLATE_PREFIX)
        second = evolve(make_section(page=pages[2], kinds=PLATES, minutes=1), prefix='Tab. ')

        assert _labels(pages, first, second) == [FIRST_PLATE, 'Plate 2', '', 'Tab. 1']

    def test_a_series_that_is_not_counted_keeps_its_pages_out_of_the_text_count(self) -> None:
        """Verify skipped kinds take no number and do not move the numbers of the pages around them."""
        pages = _book(PageKind.TEXT, PageKind.PLATE, PageKind.TEXT)
        text = make_section(page=pages[0])
        skipped = make_section(page=pages[0], display=NumberDisplay.NOT_COUNTED, kinds=PLATES)

        assert _labels(pages, text, skipped) == ['1', '', '2']

    def test_a_page_kept_out_of_the_book_takes_no_number_and_is_not_counted(self) -> None:
        """Verify a page that is not included gets no label, and the pages after it count on without it."""
        pages = _book(*[PageKind.TEXT] * 3)
        pages[1] = evolve(pages[1], included=False)

        assert _labels(pages, make_section(page=pages[0])) == ['1', '', '2']

    def test_a_label_written_by_hand_stays_and_the_page_still_counts(self) -> None:
        """Verify an exception keeps its label, and the pages after it are numbered as if it had its number."""
        pages = _book(*[PageKind.TEXT] * 3)
        pages[1] = evolve(pages[1], label=HAND_WRITTEN, label_manual=True)

        assert _labels(pages, make_section(page=pages[0])) == ['1', HAND_WRITTEN, '3']

    def test_a_hand_written_label_stays_on_a_page_kept_out_of_the_book(self) -> None:
        """Verify an exception is kept whether or not its page takes part in the count."""
        pages = _book(*[PageKind.TEXT] * 2)
        pages[1] = evolve(pages[1], included=False, label=HAND_WRITTEN, label_manual=True)

        assert _labels(pages, make_section(page=pages[0])) == ['1', HAND_WRITTEN]

    def test_a_label_that_is_not_by_hand_is_replaced_by_the_computed_one(self) -> None:
        """Verify a stale label of a page that is not an exception does not survive the computation."""
        pages = _book(*[PageKind.TEXT] * 2)
        pages[0] = evolve(pages[0], label='stale')

        assert _labels(pages, make_section(page=pages[0])) == ['1', '2']

    def test_a_section_that_starts_at_no_page_of_the_book_is_ignored(self) -> None:
        """Verify a section of another book, which names a page this book lacks, takes no part."""
        pages = _book(*[PageKind.TEXT] * 2)
        [stranger] = _book(PageKind.TEXT)

        assert _labels(pages, make_section(page=stranger)) == ['', '']

    def test_a_number_that_the_style_cannot_write_is_refused(self) -> None:
        """Verify a Roman section that runs past 3999 raises, which the service reports as a conflict."""
        pages = _book(*[PageKind.TEXT] * 2)
        section = evolve(make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER), start=ROMAN_LIMIT)

        with pytest.raises(ValueError, match=str(ROMAN_LIMIT + 1)):
            _labels(pages, section)


def _section_ids(pages: list[Page], *sections: PaginationSection) -> list[PaginationSectionId | None]:
    """Find the section that governs each page, in book order.

    :param pages: Pages of the book in book order.
    :type pages: list[Page]
    :param sections: Sections of the book.
    :type sections: PaginationSection
    :returns: The identifier of the governing section of each page, or None.
    :rtype: list[PaginationSectionId | None]
    """
    governing = Pagination(sections, pages).section_ids()
    return [governing[page.id] for page in pages]


class TestPaginationSectionIds:
    """Tests for Pagination.section_ids()."""

    def test_each_page_belongs_to_the_section_it_starts_in_until_the_next_one(self) -> None:
        """Verify the pages of the main flow are governed by the section that started last."""
        pages = _book(*[PageKind.TEXT] * 4)
        front = make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER)
        text = make_section(page=pages[2])

        assert _section_ids(pages, text, front) == [front.id, front.id, text.id, text.id]

    def test_a_series_governs_the_pages_of_its_kinds_and_the_main_flow_the_rest(self) -> None:
        """Verify plates belong to their series from its first page, while the text stays with the main flow."""
        pages = _book(PageKind.PLATE, PageKind.TEXT, PageKind.PLATE, PageKind.TEXT)
        text = make_section(page=pages[0])
        plates = make_section(page=pages[1], kinds=PLATES)

        # The plate before the series follows the main flow, as it does in the numbering
        assert _section_ids(pages, text, plates) == [text.id, text.id, plates.id, text.id]

    def test_a_series_that_starts_with_the_main_flow_takes_only_its_kinds(self) -> None:
        """Verify a series and the main flow that start on one page share the pages by kind."""
        pages = _book(PageKind.TEXT, PageKind.PLATE)
        text = make_section(page=pages[0])
        plates = make_section(page=pages[0], kinds=PLATES)

        assert _section_ids(pages, plates, text) == [text.id, plates.id]

    def test_a_page_kept_out_of_the_book_has_no_section_and_the_others_keep_theirs(self) -> None:
        """Verify a page that is not included is governed by nothing, though it stands inside a section."""
        pages = _book(*[PageKind.TEXT] * 3)
        pages[1] = evolve(pages[1], included=False)
        text = make_section(page=pages[0])

        assert _section_ids(pages, text) == [text.id, None, text.id]

    def test_a_page_before_every_section_has_no_section(self) -> None:
        """Verify the pages that stand before the first section of the main flow belong to none."""
        pages = _book(*[PageKind.TEXT] * 3)
        text = make_section(page=pages[1])

        assert _section_ids(pages, text) == [None, text.id, text.id]

    def test_a_page_of_a_section_that_is_not_counted_still_belongs_to_it(self) -> None:
        """Verify a cover section governs its pages although they take no number."""
        pages = _book(PageKind.COVER, PageKind.TEXT)
        cover = make_section(page=pages[0], display=NumberDisplay.NOT_COUNTED)
        text = make_section(page=pages[1])

        assert _section_ids(pages, cover, text) == [cover.id, text.id]

    def test_a_book_without_sections_and_a_section_of_another_book_govern_nothing(self) -> None:
        """Verify no page has a section when none starts in the book."""
        pages = _book(*[PageKind.TEXT] * 2)
        [stranger] = _book(PageKind.TEXT)

        assert _section_ids(pages) == [None, None]
        assert _section_ids(pages, make_section(page=stranger)) == [None, None]

    def test_the_sections_are_found_although_a_number_cannot_be_written(self) -> None:
        """Verify reading the sections of the pages never fails on a number that labels would refuse."""
        pages = _book(*[PageKind.TEXT] * 2)
        section = evolve(make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER), start=ROMAN_LIMIT)

        assert _section_ids(pages, section) == [section.id, section.id]


class TestLabeling:
    """Tests for Pagination.labeling()."""

    def test_roman_front_matter_and_arabic_text_are_two_rules(self) -> None:
        """Verify a book numbered in two sections gives one rule for each, starting at the page its section does."""
        pages = _book(*[PageKind.TEXT] * 4)
        preface = make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER)
        text = make_section(page=pages[2])

        labeling = Pagination([preface, text], pages).labeling()

        expect(
            list(labeling.rules)
            == [
                PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
                PageLabelRule(first_index=2, style=LabelStyle.ARABIC),
            ]
        )
        expect(
            [(number.index, number.label) for number in labeling.printed] == [(0, 'i'), (1, 'ii'), (2, '1'), (3, '2')]
        )
        assert_expectations()

    def test_a_label_by_hand_is_a_rule_of_its_own_and_the_section_goes_on_after_it(self) -> None:
        """Verify an exception is written as a prefix with no number, and its section resumes at the number it reached."""
        pages = _book(*[PageKind.TEXT] * 3)
        pages[1] = evolve(pages[1], label=HAND_WRITTEN, label_manual=True)

        labeling = Pagination([make_section(page=pages[0])], pages).labeling()

        expect(
            list(labeling.rules)
            == [
                PageLabelRule(first_index=0, style=LabelStyle.ARABIC),
                PageLabelRule(first_index=1, style=LabelStyle.NONE, prefix=HAND_WRITTEN),
                PageLabelRule(first_index=2, style=LabelStyle.ARABIC, start=3),
            ]
        )
        expect([number.label for number in labeling.printed] == ['1', HAND_WRITTEN, '3'])
        assert_expectations()

    def test_a_page_kept_out_of_the_book_is_not_in_the_output(self) -> None:
        """Verify positions count the pages that stay, so a page kept out neither breaks a run nor takes a position."""
        pages = _book(*[PageKind.TEXT] * 3)
        pages[1] = evolve(pages[1], included=False)

        labeling = Pagination([make_section(page=pages[0])], pages).labeling()

        expect(list(labeling.rules) == [PageLabelRule(first_index=0, style=LabelStyle.ARABIC)])
        expect(list(labeling.printed) == [PrintedNumber(index=0, label='1'), PrintedNumber(index=1, label='2')])
        assert_expectations()

    def test_pages_that_count_without_printing_are_numbered_by_a_rule_and_print_nothing(self) -> None:
        """Verify a section that counts and does not print gives a rule, and a section that does not count gives none."""
        pages = _book(*[PageKind.TEXT] * 3)
        counted = make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER, display=NumberDisplay.COUNTED)
        silent = make_section(page=pages[2], display=NumberDisplay.NOT_COUNTED)

        labeling = Pagination([counted, silent], pages).labeling()

        expect(
            list(labeling.rules)
            == [
                PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
                PageLabelRule(first_index=2, style=LabelStyle.NONE),
            ]
        )
        expect(list(labeling.printed) == [])
        assert_expectations()

    def test_a_series_by_kind_interrupts_the_rule_of_the_text_and_the_text_resumes(self) -> None:
        """Verify plates in the middle of the text are a rule with their prefix, after which the text goes on."""
        pages = _book(PageKind.TEXT, PageKind.PLATE, PageKind.TEXT)
        text = make_section(page=pages[0])
        plates = evolve(make_section(page=pages[0], style=LabelStyle.ROMAN_UPPER, kinds=PLATES), prefix=PLATE_PREFIX)

        labeling = Pagination([text, plates], pages).labeling()

        assert list(labeling.rules) == [
            PageLabelRule(first_index=0, style=LabelStyle.ARABIC),
            PageLabelRule(first_index=1, style=LabelStyle.ROMAN_UPPER, prefix=PLATE_PREFIX),
            PageLabelRule(first_index=2, style=LabelStyle.ARABIC, start=2),
        ]

    def test_a_book_without_sections_has_one_rule_that_writes_nothing(self) -> None:
        """Verify every page of a book that is not paginated is covered by an empty label, and nothing is printed."""
        labeling = Pagination([], _book(*[PageKind.TEXT] * 2)).labeling()

        expect(list(labeling.rules) == [PageLabelRule(first_index=0, style=LabelStyle.NONE)])
        expect(list(labeling.printed) == [])
        assert_expectations()
