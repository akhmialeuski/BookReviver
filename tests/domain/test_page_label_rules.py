"""Tests for the page label rules of the domain, which a PDF keeps and a pagination section can be made from."""

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ColorMode, LabelStyle, SourceKind
from bookreviver.domain.page_label_rules import PageLabelRule
from bookreviver.domain.values import ScanFacts, SourceAnalysis

ROMAN_LIMIT: int = 3999
SCAN_COUNT: int = 4


def _analysis(*rules: PageLabelRule) -> SourceAnalysis:
    """Describe a source of four scans that carries the given rules.

    :param rules: The label rules of the source.
    :type rules: PageLabelRule
    :returns: The analysis of the source.
    :rtype: SourceAnalysis
    """
    scans = [ScanFacts(width_px=10, height_px=10, color_mode=ColorMode.GRAY) for _ in range(SCAN_COUNT)]
    return SourceAnalysis(kind=SourceKind.PDF, scans=scans, label_rules=rules)


class TestPageLabelRule:
    """Tests for PageLabelRule."""

    @pytest.mark.parametrize(
        ('style', 'start', 'count', 'fits'),
        [
            (LabelStyle.ARABIC, 1, 10, True),
            (LabelStyle.ROMAN_LOWER, 1, ROMAN_LIMIT, True),
            (LabelStyle.ROMAN_LOWER, 1, ROMAN_LIMIT + 1, False),
            (LabelStyle.ARABIC, 0, 3, False),
            (LabelStyle.NONE, 1, 3, False),
            (LabelStyle.ALPHA_UPPER, 5, 100, True),
        ],
    )
    def test_fits_tells_whether_a_section_can_number_the_run(
        self, style: LabelStyle, start: int, count: int, *, fits: bool
    ) -> None:
        """Verify a run is a section only when the style writes numbers and every number of the run can be written.

        :param style: Style of the rule.
        :type style: LabelStyle
        :param start: Number of the first page of the run.
        :type start: int
        :param count: Number of pages of the run.
        :type count: int
        :param fits: Whether a section can number the run.
        :type fits: bool
        """
        assert PageLabelRule(first_index=0, style=style, start=start).fits(count) is fits

    @pytest.mark.parametrize(
        ('style', 'prefix', 'start', 'index', 'expected'),
        [
            (LabelStyle.ROMAN_LOWER, '', 1, 2, 'iii'),
            (LabelStyle.ARABIC, 'A-', 5, 1, 'A-6'),
            (LabelStyle.NONE, '12a', 1, 3, '12a'),
            (LabelStyle.ARABIC, 'p', 0, 0, 'p'),
        ],
    )
    def test_label_at_writes_the_prefix_and_the_number_of_the_page(
        self, style: LabelStyle, prefix: str, start: int, index: int, expected: str
    ) -> None:
        """Verify the label of a page of a run, counted from the run's first page, with the prefix alone as a fallback.

        :param style: Style of the rule.
        :type style: LabelStyle
        :param prefix: Prefix of the rule.
        :type prefix: str
        :param start: Number of the first page of the run.
        :type start: int
        :param index: Position of the page among the pages of the file; the run starts at 0.
        :type index: int
        :param expected: The label of the page.
        :type expected: str
        """
        rule = PageLabelRule(first_index=0, style=style, prefix=prefix, start=start)

        assert rule.label_at(index) == expected

    def test_continues_needs_the_same_style_prefix_and_the_next_number(self) -> None:
        """Verify a page continues a run only in the same style and prefix at the number the run would give it."""
        rule = PageLabelRule(first_index=2, style=LabelStyle.ARABIC, prefix='A', start=7)

        expect(rule.continues(4, style=LabelStyle.ARABIC, prefix='A', number=9) is True)
        expect(rule.continues(4, style=LabelStyle.ARABIC, prefix='A', number=10) is False)
        expect(rule.continues(4, style=LabelStyle.ARABIC, prefix='B', number=9) is False)
        expect(rule.continues(4, style=LabelStyle.ROMAN_LOWER, prefix='A', number=9) is False)
        assert_expectations()

    def test_a_prefix_only_run_continues_whatever_the_number(self) -> None:
        """Verify a style that writes no number continues a run by its prefix alone."""
        rule = PageLabelRule(first_index=0, style=LabelStyle.NONE, prefix='x')

        assert rule.continues(5, style=LabelStyle.NONE, prefix='x', number=1) is True

    def test_a_rule_cannot_start_before_the_first_page(self) -> None:
        """Verify a negative position is refused."""
        with pytest.raises(ValueError, match='first_index'):
            PageLabelRule(first_index=-1, style=LabelStyle.ARABIC)


class TestSourceAnalysisRules:
    """Tests for the rules of a SourceAnalysis."""

    def test_rules_that_start_in_order_at_scans_are_accepted(self) -> None:
        """Verify an analysis keeps the rules it was given."""
        rules = [
            PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
            PageLabelRule(first_index=2, style=LabelStyle.ARABIC),
        ]

        assert list(_analysis(*rules).label_rules) == rules

    @pytest.mark.parametrize('starts', [[2, 1], [1, 1], [SCAN_COUNT]], ids=['reversed', 'repeated', 'past-the-end'])
    def test_rules_out_of_order_or_past_the_scans_are_refused(self, starts: list[int]) -> None:
        """Verify rules must start at increasing positions of the scans.

        :param starts: Positions the rules start at.
        :type starts: list[int]
        """
        rules = [PageLabelRule(first_index=start, style=LabelStyle.ARABIC) for start in starts]

        with pytest.raises(ValueError, match='increasing positions'):
            _analysis(*rules)
