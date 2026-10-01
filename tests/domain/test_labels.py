"""Tests for the page numbers of the domain: their styles, the numbering of a range, and the labels of a source."""

from typing import NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import ColorMode, LabelStyle, PageKind, SourceKind
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import PageNumbering, ScanFacts, SourceAnalysis
from tests.helpers.builders import make_page, make_project, new_account_id


class StyleCase(NamedTuple):
    """A number and how every style writes it.

    :ivar number: The page number.
    :ivar arabic: Its Arabic form.
    :ivar lower: Its lower-case Roman form.
    """

    number: int
    arabic: str
    lower: str


NUMBERS: list[StyleCase] = [
    StyleCase(1, '1', 'i'),
    StyleCase(4, '4', 'iv'),
    StyleCase(9, '9', 'ix'),
    StyleCase(12, '12', 'xii'),
    StyleCase(14, '14', 'xiv'),
    StyleCase(40, '40', 'xl'),
    StyleCase(90, '90', 'xc'),
    StyleCase(400, '400', 'cd'),
    StyleCase(1994, '1994', 'mcmxciv'),
    StyleCase(3999, '3999', 'mmmcmxcix'),
]
BOOK_SIZE: int = 3
ROMAN_LIMIT: int = 3999
FIRST_ID, LAST_ID = PageId(uuid4()), PageId(uuid4())


class TestFormat:
    """Tests for LabelStyle.write()."""

    @pytest.mark.parametrize('case', NUMBERS, ids=[str(case.number) for case in NUMBERS])
    def test_writes_a_number_in_every_style(self, case: StyleCase) -> None:
        """Verify the Arabic form, both Roman forms and the empty label of the style that erases.

        :param case: A number and its forms.
        :type case: StyleCase
        """
        expect(LabelStyle.ARABIC.write(case.number) == case.arabic)
        expect(LabelStyle.ROMAN_LOWER.write(case.number) == case.lower)
        expect(LabelStyle.ROMAN_UPPER.write(case.number) == case.lower.upper())
        expect(LabelStyle.NONE.write(case.number) == '')
        assert_expectations()

    def test_arabic_numbers_may_pass_the_roman_limit(self) -> None:
        """Verify only the Roman styles stop at 3999, since a book may have more pages than that."""
        assert LabelStyle.ARABIC.write(ROMAN_LIMIT + 1) == str(ROMAN_LIMIT + 1)

    @pytest.mark.parametrize('style', [LabelStyle.ROMAN_LOWER, LabelStyle.ROMAN_UPPER])
    @pytest.mark.parametrize('number', [0, -1, ROMAN_LIMIT + 1])
    def test_a_number_without_a_roman_numeral_is_refused(self, style: LabelStyle, number: int) -> None:
        """Verify zero, a negative number and 4000 have no Roman numeral.

        :param style: A Roman style.
        :type style: LabelStyle
        :param number: A number the numerals cannot write.
        :type number: int
        """
        with pytest.raises(ValueError, match=str(number)):
            style.write(number)

    def test_arabic_zero_is_refused(self) -> None:
        """Verify no style writes a page number below 1, except the one that writes nothing."""
        with pytest.raises(ValueError, match='0'):
            LabelStyle.ARABIC.write(0)


class TestPageNumbering:
    """Tests for PageNumbering."""

    @pytest.mark.parametrize(
        ('style', 'bracketed', 'expected'),
        [
            (LabelStyle.ARABIC, False, '12'),
            (LabelStyle.ARABIC, True, '[12]'),
            (LabelStyle.ROMAN_LOWER, False, 'xii'),
            (LabelStyle.ROMAN_LOWER, True, '[xii]'),
            (LabelStyle.ROMAN_UPPER, False, 'XII'),
            (LabelStyle.ROMAN_UPPER, True, '[XII]'),
            (LabelStyle.NONE, False, ''),
            (LabelStyle.NONE, True, ''),
        ],
    )
    def test_label_writes_the_number_in_its_style_with_or_without_brackets(
        self, *, style: LabelStyle, bracketed: bool, expected: str
    ) -> None:
        """Verify the brackets enclose a written number and never an empty label.

        :param style: Style of the numbering.
        :type style: LabelStyle
        :param bracketed: Whether the numbering brackets its labels.
        :type bracketed: bool
        :param expected: The label of the number 12.
        :type expected: str
        """
        numbering = PageNumbering(first_page_id=FIRST_ID, last_page_id=LAST_ID, style=style, bracketed=bracketed)

        assert numbering.label(12) == expected

    def test_defaults_number_from_one_and_skip_no_kind(self) -> None:
        """Verify a numbering that names only its range starts at 1, without brackets, and skips nothing."""
        numbering = PageNumbering(first_page_id=FIRST_ID, last_page_id=LAST_ID, style=LabelStyle.ARABIC)

        expect(numbering.start == 1)
        expect(numbering.bracketed is False)
        expect(numbering.skip_kinds == frozenset())
        assert_expectations()

    def test_start_below_one_is_refused(self) -> None:
        """Verify the first number is at least 1."""
        with pytest.raises(ValueError, match='start'):
            PageNumbering(first_page_id=FIRST_ID, last_page_id=LAST_ID, style=LabelStyle.ARABIC, start=0)

    def test_roman_start_past_the_limit_is_refused(self) -> None:
        """Verify a Roman numbering cannot begin where the numerals end."""
        with pytest.raises(ValueError, match=str(ROMAN_LIMIT + 1)):
            PageNumbering(
                first_page_id=FIRST_ID, last_page_id=LAST_ID, style=LabelStyle.ROMAN_UPPER, start=ROMAN_LIMIT + 1
            )


class TestPageChanges:
    """Tests for PageChanges.apply_to()."""

    def test_replaces_given_fields_and_keeps_the_rest(self) -> None:
        """Verify a field left as None stays, and an empty string clears a label."""
        page = evolve(make_page(project_id=make_project(owner_id=new_account_id()).id), label='xii', notes='Stamp')

        changed = PageChanges(label='', kind=PageKind.PLATE, included=False).apply_to(page)

        expect((changed.label, changed.kind, changed.included) == ('', PageKind.PLATE, False))
        expect(changed.notes == 'Stamp')
        expect(evolve(changed, label='xii', kind=page.kind, included=True) == page)
        assert_expectations()

    def test_changes_nothing_by_default(self) -> None:
        """Verify a change that names no field returns an equal page."""
        page = make_page(project_id=make_project(owner_id=new_account_id()).id)

        assert PageChanges().apply_to(page) == page


class TestSourceAnalysis:
    """Tests for the page labels of SourceAnalysis."""

    @staticmethod
    def _scans(count: int) -> list[ScanFacts]:
        """Build facts for ``count`` scans.

        :param count: Number of scans.
        :type count: int
        :returns: The facts of that many gray scans.
        :rtype: list[ScanFacts]
        """
        return [ScanFacts(width_px=10, height_px=10, color_mode=ColorMode.GRAY) for _ in range(count)]

    def test_label_of_gives_the_label_of_a_scan_or_empty_without_labels(self) -> None:
        """Verify a source with labels answers by scan number, and one without answers an empty label."""
        labelled = SourceAnalysis(kind=SourceKind.PDF, scans=self._scans(BOOK_SIZE), scan_labels=['i', 'ii', ''])
        bare = SourceAnalysis(kind=SourceKind.IMAGE, scans=self._scans(BOOK_SIZE))

        expect([labelled.label_of(number) for number in range(BOOK_SIZE)] == ['i', 'ii', ''])
        expect([bare.label_of(number) for number in range(BOOK_SIZE)] == [''] * BOOK_SIZE)
        assert_expectations()

    def test_labels_that_do_not_fit_the_scans_are_refused(self) -> None:
        """Verify there are as many labels as scans, or none."""
        with pytest.raises(ValueError, match='page labels'):
            SourceAnalysis(kind=SourceKind.PDF, scans=self._scans(BOOK_SIZE), scan_labels=['i'])
