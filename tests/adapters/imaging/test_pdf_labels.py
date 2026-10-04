"""Tests for the page labels of a PDF: reading the rules of a file, and writing the rules of a book into one."""

from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.imaging.pdf_labels import (
    PdfLabelKey,
    PdfLabelWriter,
    pdf_label_entries,
    pdf_text,
    read_label_rules,
)
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.enums import LabelStyle, PageKind, SourceKind
from bookreviver.domain.page_label_rules import BookLabeling, PageLabelRule
from bookreviver.domain.pagination import Pagination
from tests.adapters.imaging.samples import (
    ROMAN_THEN_ARABIC_RULES,
    PdfPage,
    ScanImage,
    add_page_labels,
    write_pdf,
)
from tests.helpers.builders import make_page, make_project, make_section, new_account_id

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from bookreviver.ports.imaging import SourceInspector

pytestmark = pytest.mark.anyio

JPEG: str = 'JPEG'
PDF_NAME: str = 'book.pdf'
SCAN_SIZE_PX: tuple[int, int] = (300, 400)
BOOK_PAGES: int = 5
HAND_WRITTEN: str = '12a'


def _entry(start: int, style: str = '', prefix: str = '', first: int = 1) -> dict[str, Any]:
    """Build a dictionary as ``Document.get_page_labels`` returns it.

    :param start: Page the rule starts at.
    :type start: int
    :param style: Style letter of the rule, or empty.
    :type style: str
    :param prefix: Prefix of the rule.
    :type prefix: str
    :param first: Number of the first page of the run.
    :type first: int
    :returns: The rule as PyMuPDF spells it.
    :rtype: dict[str, Any]
    """
    return {
        PdfLabelKey.START_PAGE: start,
        PdfLabelKey.PREFIX: prefix,
        PdfLabelKey.STYLE: style,
        PdfLabelKey.FIRST_NUMBER: first,
    }


def _pdf(directory: Path, rules: Sequence[Mapping[str, Any]] = ()) -> Path:
    """Write a PDF of five scan pages, with the given page label rules when there are any.

    :param directory: Directory to write the file in.
    :type directory: Path
    :param rules: Rules in the form of ``Document.set_page_labels``.
    :type rules: Sequence[Mapping[str, Any]]
    :returns: Path of the file.
    :rtype: Path
    """
    pages = [PdfPage(images=[ScanImage(mode='L', size_px=SCAN_SIZE_PX, image_format=JPEG)])] * BOOK_PAGES
    path = write_pdf(directory / PDF_NAME, pages=pages)
    return add_page_labels(path, rules=rules) if rules else path


class TestReadLabelRules:
    """Tests for read_label_rules()."""

    def test_the_style_letters_become_the_styles_of_the_domain(self) -> None:
        """Verify each letter of PDF 32000 reads as its style, and an unknown letter reads as a prefix alone."""
        entries = [
            _entry(0, 'D'),
            _entry(1, 'R'),
            _entry(2, 'r'),
            _entry(3, 'A'),
            _entry(4, 'a'),
            _entry(5, 'X', prefix='x'),
        ]

        rules = read_label_rules(entries, page_count=6)

        assert [rule.style for rule in rules] == [
            LabelStyle.ARABIC,
            LabelStyle.ROMAN_UPPER,
            LabelStyle.ROMAN_LOWER,
            LabelStyle.ALPHA_UPPER,
            LabelStyle.ALPHA_LOWER,
            LabelStyle.NONE,
        ]

    def test_rules_come_in_page_order_and_a_rule_past_the_last_page_is_dropped(self) -> None:
        """Verify the rules are sorted by their start and one that starts after the last page is left out."""
        rules = read_label_rules([_entry(2, 'D'), _entry(0, 'r'), _entry(9, 'D')], page_count=4)

        assert [(rule.first_index, rule.style) for rule in rules] == [
            (0, LabelStyle.ROMAN_LOWER),
            (2, LabelStyle.ARABIC),
        ]

    def test_the_prefix_and_the_first_number_are_kept(self) -> None:
        """Verify a rule keeps its prefix and the number of its first page."""
        [rule] = read_label_rules([_entry(0, 'D', prefix='A-', first=7)], page_count=2)

        expect(rule == PageLabelRule(first_index=0, style=LabelStyle.ARABIC, prefix='A-', start=7))
        assert_expectations()

    @pytest.mark.parametrize(
        ('shown', 'text'),
        [('<feff04220430>', '\u0422\u0430'), ('<41>', 'A'), ('<4>', '@'), ('plain', 'plain')],
        ids=['utf-16', 'latin-1', 'odd-digits', 'literal'],
    )
    def test_a_prefix_shown_as_hexadecimal_digits_is_decoded(self, shown: str, text: str) -> None:
        """Verify the form PyMuPDF shows a non-ASCII string in is turned back into text.

        :param shown: The prefix as PyMuPDF returns it.
        :type shown: str
        :param text: The text of the prefix.
        :type text: str
        """
        [rule] = read_label_rules([_entry(0, 'D', prefix=shown)], page_count=1)

        assert rule.prefix == text


class TestPdfText:
    """Tests for pdf_text()."""

    @pytest.mark.parametrize(
        ('text', 'expected'),
        [
            ('Plate ', 'Plate '),
            ('(a)', '\\050a\\051'),
            ('a\\b', 'a\\134b'),
            ('line\nbreak', 'line\\012break'),
            ('\u0422', '\\376\\377\\004"'),
        ],
        ids=['plain', 'parentheses', 'backslash', 'control', 'non-ascii'],
    )
    def test_text_is_written_as_the_inside_of_a_literal_string(self, text: str, expected: str) -> None:
        """Verify what a literal string cannot hold is written as an octal escape, a non-ASCII text in UTF-16.

        :param text: The text of a prefix.
        :type text: str
        :param expected: The inside of the literal string.
        :type expected: str
        """
        assert pdf_text(text) == expected


class TestPdfLabelEntries:
    """Tests for pdf_label_entries()."""

    def test_rules_become_the_dictionaries_of_set_page_labels(self) -> None:
        """Verify the style letters, the escaped prefix and the first numbers of the rules."""
        rules = [
            PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
            PageLabelRule(first_index=3, style=LabelStyle.ARABIC, prefix='(x)', start=4),
            PageLabelRule(first_index=4, style=LabelStyle.NONE, prefix=HAND_WRITTEN),
        ]

        assert pdf_label_entries(rules) == [
            _entry(0, 'r'),
            _entry(3, 'D', prefix='\\050x\\051', first=4),
            _entry(4, '', prefix=HAND_WRITTEN),
        ]


class TestPdfLabelWriter:
    """Tests for PdfLabelWriter."""

    async def test_the_rules_written_are_the_rules_read_back(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify rules in every style, with a prefix and a first number, survive a write and an inspection.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = _pdf(tmp_path)
        rules = [
            PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
            PageLabelRule(first_index=2, style=LabelStyle.ARABIC, start=5),
            PageLabelRule(first_index=3, style=LabelStyle.ALPHA_UPPER, prefix='Plate '),
            PageLabelRule(first_index=4, style=LabelStyle.NONE, prefix=HAND_WRITTEN),
        ]

        await PdfLabelWriter().write(path, BookLabeling(rules=rules))
        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(list(analysis.label_rules) == rules)
        expect(list(analysis.scan_labels) == ['i', 'ii', '5', 'Plate A', HAND_WRITTEN])
        assert_expectations()

    async def test_a_second_write_replaces_the_rules_of_the_first(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify the rules a file had are not kept beside the new ones.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = _pdf(tmp_path, ROMAN_THEN_ARABIC_RULES)
        replacement = [PageLabelRule(first_index=0, style=LabelStyle.ALPHA_LOWER)]

        await PdfLabelWriter().write(path, BookLabeling(rules=replacement))
        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        assert list(analysis.label_rules) == replacement

    async def test_a_rule_past_the_last_page_is_refused(self, tmp_path: Path) -> None:
        """Verify the numbering of a longer book is not written into a shorter file.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = _pdf(tmp_path)
        rules = [PageLabelRule(first_index=BOOK_PAGES, style=LabelStyle.ARABIC)]

        with pytest.raises(ValueError, match='no rule starts'):
            await PdfLabelWriter().write(path, BookLabeling(rules=rules))

    async def test_the_sections_of_a_book_survive_a_trip_through_a_pdf(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify the labels of a paginated book, with an exception in it, are the labels read from the file it is written to.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        project = make_project(owner_id=new_account_id())
        keys = FractionalOrderKeys().spread(lower=None, upper=None, count=BOOK_PAGES)
        pages = [make_page(project_id=project.id, order_key=key, kind=PageKind.TEXT) for key in keys]
        pages[3] = evolve(pages[3], label=HAND_WRITTEN, label_manual=True)
        sections = [make_section(page=pages[0], style=LabelStyle.ROMAN_LOWER), make_section(page=pages[2])]
        pagination = Pagination(sections, pages)
        path = _pdf(tmp_path)

        await PdfLabelWriter().write(path, pagination.labeling())
        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        labels = pagination.labels()
        assert list(analysis.scan_labels) == [labels[page.id] for page in pages]
