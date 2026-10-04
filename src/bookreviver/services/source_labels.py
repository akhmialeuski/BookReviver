"""The pagination sections that the page labels of an imported source make, and the labels that stay exceptions.

A source such as a PDF can carry page label rules: Roman numerals for the preface, Arabic ones from 1 for the text. At
import each rule that a section can express becomes a section of the book that starts at the first scan of its run, so
the book is paginated as the file was and the numbers follow the pages when they are inserted, deleted or moved. A page
whose label is not what its section gives, such as ``12a`` or a label with a prefix and no number, keeps its label as an
exception that no recompute changes. A rule a section cannot express becomes a section that does not count, so its
pages stay outside the count of the pages before them and after them.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import PaginationSection
from bookreviver.domain.enums import LabelStyle, NumberDisplay
from bookreviver.domain.ids import PaginationSectionId
from bookreviver.domain.page_label_rules import PageLabelRule
from bookreviver.domain.pagination import Pagination

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.entities import Page


class SourceLabels:
    """The sections and the page exceptions that the label rules of one source give the pages of its scans.

    :ivar sections: The sections to add to the book, in the order of the pages they start at; none without rules.
    :ivar pages: The pages given, with ``label_manual`` set on those whose label their section does not give.
    """

    def __init__(self, *, rules: Sequence[PageLabelRule], pages: Sequence[Page], moment: datetime) -> None:
        """Make the sections of the rules and find which labels they do not give.

        Pages before the first rule are outside every rule of the file, so they are made a section that does not count.
        Without rules there are no sections, and a label of a scan stays an exception, as it is without a rule.

        :param rules: The rules of the source, in the order of its scans, as ``SourceAnalysis.label_rules``.
        :type rules: Sequence[PageLabelRule]
        :param pages: The pages of the scans of the source in book order, each with the label its scan carries.
        :type pages: Sequence[Page]
        :param moment: Time to stamp the sections with.
        :type moment: datetime
        """
        self.sections: list[PaginationSection] = []
        self.pages: list[Page] = list(pages)
        if not rules:
            return
        unnumbered = [PageLabelRule(first_index=0, style=LabelStyle.NONE)] if rules[0].first_index > 0 else []
        runs = [*unnumbered, *rules]
        ends = [*(rule.first_index for rule in runs[1:]), len(pages)]
        for rule, end in zip(runs, ends, strict=True):
            counts = rule.fits(end - rule.first_index)
            first = pages[rule.first_index]
            self.sections.append(
                PaginationSection(
                    id=PaginationSectionId(uuid4()),
                    project_id=first.project_id,
                    first_page_id=first.id,
                    name=rule.style.label if counts else NumberDisplay.NOT_COUNTED.label,
                    style=rule.style if counts else LabelStyle.NONE,
                    start=rule.start if counts else 1,
                    prefix=rule.prefix if counts else '',
                    display=NumberDisplay.PRINTED if counts else NumberDisplay.NOT_COUNTED,
                    created_at=moment,
                    updated_at=moment,
                )
            )
        computed = Pagination(self.sections, [evolve(page, label_manual=False) for page in pages]).labels()
        self.pages = [evolve(page, label_manual=bool(page.label) and page.label != computed[page.id]) for page in pages]
