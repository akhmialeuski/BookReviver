"""Tests for the pagination sections that the page label rules of an imported source make."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.domain.enums import LabelStyle, NumberDisplay
from bookreviver.domain.page_label_rules import PageLabelRule
from bookreviver.services.source_labels import SourceLabels
from tests.helpers.builders import EPOCH, make_page, make_project, new_account_id

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page

HAND_WRITTEN: str = '12a'
ROMAN_LIMIT: int = 3999


def _pages(*labels: str) -> list[Page]:
    """Build the pages of one source's scans, each carrying the label its scan gave it.

    :param labels: Label of each page in book order, empty for none.
    :type labels: str
    :returns: Pages whose labels are marked as written by hand, as an import marks a label that comes with a scan.
    :rtype: list[Page]
    """
    project = make_project(owner_id=new_account_id())
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(labels))
    return [
        evolve(make_page(project_id=project.id, order_key=key), label=label, label_manual=bool(label))
        for key, label in zip(keys, labels, strict=True)
    ]


class TestSourceLabels:
    """Tests for SourceLabels."""

    def test_a_source_without_rules_makes_no_section_and_keeps_its_labels(self) -> None:
        """Verify the labels a scan carries stay exceptions when the file has no rules to make sections of."""
        pages = _pages('i', '')

        labels = SourceLabels(rules=[], pages=pages, moment=EPOCH)

        expect(labels.sections == [])
        expect(labels.pages == pages)
        assert_expectations()

    def test_each_rule_makes_a_printed_section_that_starts_at_its_first_page(self) -> None:
        """Verify the sections have the style, prefix and first number of their rules, and start where the rules do."""
        pages = _pages('i', 'ii', 'A-5', 'A-6')
        rules = [
            PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
            PageLabelRule(first_index=2, style=LabelStyle.ARABIC, prefix='A-', start=5),
        ]

        labels = SourceLabels(rules=rules, pages=pages, moment=EPOCH)

        expect([section.first_page_id for section in labels.sections] == [pages[0].id, pages[2].id])
        expect([section.style for section in labels.sections] == [LabelStyle.ROMAN_LOWER, LabelStyle.ARABIC])
        expect([(section.prefix, section.start) for section in labels.sections] == [('', 1), ('A-', 5)])
        expect({section.display for section in labels.sections} == {NumberDisplay.PRINTED})
        assert_expectations()

    def test_the_labels_the_sections_give_are_not_exceptions(self) -> None:
        """Verify a label that is what its section gives is left to the section."""
        pages = _pages('i', 'ii', '1')
        rules = [
            PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
            PageLabelRule(first_index=2, style=LabelStyle.ARABIC),
        ]

        labels = SourceLabels(rules=rules, pages=pages, moment=EPOCH)

        assert [page.label_manual for page in labels.pages] == [False, False, False]

    def test_a_label_the_section_does_not_give_stays_an_exception(self) -> None:
        """Verify a label that differs from the one its section computes is kept by hand, and the others are not."""
        pages = _pages('1', HAND_WRITTEN, '3')
        rules = [PageLabelRule(first_index=0, style=LabelStyle.ARABIC)]

        labels = SourceLabels(rules=rules, pages=pages, moment=EPOCH)

        assert [page.label_manual for page in labels.pages] == [False, True, False]

    def test_a_rule_a_section_cannot_express_makes_a_section_that_does_not_count(self) -> None:
        """Verify a rule that writes a prefix alone makes a section outside the count, with its labels by hand."""
        pages = _pages('1', HAND_WRITTEN, HAND_WRITTEN)
        rules = [
            PageLabelRule(first_index=0, style=LabelStyle.ARABIC),
            PageLabelRule(first_index=1, style=LabelStyle.NONE, prefix=HAND_WRITTEN),
        ]

        labels = SourceLabels(rules=rules, pages=pages, moment=EPOCH)

        expect([section.display for section in labels.sections] == [NumberDisplay.PRINTED, NumberDisplay.NOT_COUNTED])
        expect([page.label_manual for page in labels.pages] == [False, True, True])
        assert_expectations()

    def test_pages_before_the_first_rule_make_a_section_that_does_not_count(self) -> None:
        """Verify the pages no rule governs stay out of the count that starts at the first rule."""
        pages = _pages('', '', '1')
        rules = [PageLabelRule(first_index=2, style=LabelStyle.ARABIC)]

        labels = SourceLabels(rules=rules, pages=pages, moment=EPOCH)

        expect([section.first_page_id for section in labels.sections] == [pages[0].id, pages[2].id])
        expect([section.display for section in labels.sections] == [NumberDisplay.NOT_COUNTED, NumberDisplay.PRINTED])
        expect([page.label_manual for page in labels.pages] == [False, False, False])
        assert_expectations()

    @pytest.mark.parametrize(
        ('length', 'display'),
        [(ROMAN_LIMIT, NumberDisplay.PRINTED), (ROMAN_LIMIT + 1, NumberDisplay.NOT_COUNTED)],
        ids=['fits', 'runs-past-the-numerals'],
    )
    def test_a_roman_run_past_3999_pages_is_not_a_section(self, length: int, display: NumberDisplay) -> None:
        """Verify a Roman rule whose run no section can write is made a section that does not count.

        :param length: Number of pages of the run.
        :type length: int
        :param display: What the section made of the rule shows.
        :type display: NumberDisplay
        """
        pages = _pages(*[''] * length)
        rules = [PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER)]

        labels = SourceLabels(rules=rules, pages=pages, moment=EPOCH)

        assert [section.display for section in labels.sections] == [display]
