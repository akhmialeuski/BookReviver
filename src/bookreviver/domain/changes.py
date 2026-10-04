"""Partial changes of value objects, as an edit form or a JSON Merge Patch sends them.

A change names only the fields to replace. Most changes keep a field that is left as None, and clearing such a field is
a change to its empty value, such as an empty string. The book description cannot work that way, because its height has
None as its empty value, so its change tells "not sent" from "clear" with the sentinel ``KEEP`` instead. The API turns
a field sent as null into the empty value of the field. The other field with no empty value of its own is the cover
page of a project, where None is the empty value, so its change is the object ``CoverChange``.
"""

import enum
from typing import TYPE_CHECKING

from attrs import asdict, evolve, field, frozen

from bookreviver.domain.enums import BlankFill, PageKind

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, Project
    from bookreviver.domain.enums import ContentType, ImagePolicy, Orthography, RightsStatus, Script
    from bookreviver.domain.ids import PageId
    from bookreviver.domain.values import BookDetails, BookIdentifier, Contributor


class Keep(enum.Enum):
    """The one value of ``KEEP``, so a field that may be None can still tell "left out" from "cleared"."""

    KEEP = enum.auto()


KEEP: Keep = Keep.KEEP


@frozen(kw_only=True)
class BookDetailsChanges:
    """New values for some fields of a book description; ``KEEP`` keeps the current value.

    A list is replaced as a whole, and a cleared field is set to its empty value: an empty string, an empty tuple,
    None for the height or the unknown member of an enum.

    :ivar title: New title, which may not be empty.
    :ivar subtitle: New subtitle.
    :ivar parallel_titles: New titles in other languages.
    :ivar original_title: New title of the original work.
    :ivar contributors: New people who made the book, in the order of the title page.
    :ivar publisher: New publisher.
    :ivar printer: New printing house.
    :ivar publication_place: New city of publication.
    :ivar publication_year: New year of publication as printed.
    :ivar edition: New edition statement.
    :ivar censorship: New censor's permit.
    :ivar series: New series the book belongs to.
    :ivar series_number: New number of the book in its series.
    :ivar volume: New volume or part within a multi-volume work.
    :ivar languages: New ISO 639-3 codes of the languages of the text.
    :ivar orthography: New spelling system of the print.
    :ivar script: New writing system of the print.
    :ivar printed_pagination: New pagination as a catalogue states it.
    :ivar height_cm: New height in centimetres, or None to clear it.
    :ivar illustrations: New statement of the illustrations.
    :ivar binding: New binding or cover of the copy.
    :ivar identifiers: New numbers and addresses that identify the book or the copy.
    :ivar subjects: New topics of the book.
    :ivar rights: New publishing rights.
    :ivar copy_holder: New owner of the scanned copy.
    :ivar copy_notes: New marks of the scanned copy.
    :ivar notes: New free-form notes of the owner.
    """

    title: str | Keep = KEEP
    subtitle: str | Keep = KEEP
    parallel_titles: tuple[str, ...] | Keep = KEEP
    original_title: str | Keep = KEEP
    contributors: tuple[Contributor, ...] | Keep = KEEP
    publisher: str | Keep = KEEP
    printer: str | Keep = KEEP
    publication_place: str | Keep = KEEP
    publication_year: str | Keep = KEEP
    edition: str | Keep = KEEP
    censorship: str | Keep = KEEP
    series: str | Keep = KEEP
    series_number: str | Keep = KEEP
    volume: str | Keep = KEEP
    languages: tuple[str, ...] | Keep = KEEP
    orthography: Orthography | Keep = KEEP
    script: Script | Keep = KEEP
    printed_pagination: str | Keep = KEEP
    height_cm: int | Keep | None = KEEP
    illustrations: str | Keep = KEEP
    binding: str | Keep = KEEP
    identifiers: tuple[BookIdentifier, ...] | Keep = KEEP
    subjects: tuple[str, ...] | Keep = KEEP
    rights: RightsStatus | Keep = KEEP
    copy_holder: str | Keep = KEEP
    copy_notes: str | Keep = KEEP
    notes: str | Keep = KEEP

    def apply_to(self, details: BookDetails) -> BookDetails:
        """Return ``details`` with every given field replaced, checked like a new description.

        :param details: Current description of the book.
        :type details: BookDetails
        :returns: The description with the given fields replaced and the others kept.
        :rtype: BookDetails
        :raises ValueError: If the result breaks a rule of a description, such as an empty title.
        """
        given = {name: value for name, value in asdict(self, recurse=False).items() if value is not KEEP}
        return evolve(details, **given)


@frozen
class CoverChange:
    """The new cover page of a project, or the removal of its cover.

    A cover cannot be a plain optional page in a change, because None already means that the cover stays, so a change
    of the cover is an object of its own whose page may be empty.

    :ivar page_id: Page to show as the cover, or None to fall back to the first page of the book.
    """

    page_id: PageId | None


@frozen(kw_only=True)
class ProjectChanges:
    """New values for some fields of a project; a field left as None keeps its current value.

    :ivar details: New values for some fields of the book description.
    :ivar image_policy: New way to store the images of new scans and page versions.
    :ivar cover: New cover page, or None to keep the current one.
    """

    details: BookDetailsChanges = field(factory=BookDetailsChanges)
    image_policy: ImagePolicy | None = None
    cover: CoverChange | None = None

    def apply_to(self, project: Project) -> Project:
        """Return ``project`` with every given field replaced, its description checked like a new one.

        The update time is left to the caller, which owns the clock, and a cover that is not a page of the project is
        refused by the repository that stores the result.

        :param project: Current state of the project.
        :type project: Project
        :returns: The project with the given fields replaced and the others kept.
        :rtype: Project
        :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
        """
        changed = evolve(project, details=self.details.apply_to(project.details))
        if self.image_policy is not None:
            changed = evolve(changed, image_policy=self.image_policy)
        if self.cover is not None:
            changed = evolve(changed, cover_page_id=self.cover.page_id)
        return changed


@frozen(kw_only=True)
class PageChanges:
    """New values for some fields of a page; a field left as None keeps its current value.

    A cleared label or note is an empty string, so None never has to mean "clear" here. The kind and the inclusion of a
    page have no empty value and are never cleared. A label that is given and not empty is written by hand, so it is an
    exception that the numbers computed from the sections never change, and an empty one gives the number back to the
    sections.

    :ivar label: New printed number, or an empty string to leave the number to the pagination sections.
    :ivar kind: New role of the page in the book. A page that stops being a blank page gets its scan back in place of a
                leaf.
    :ivar content_type: New content type of the page, which the user sets by hand, so the detection of the content never
                        changes it again. It has no empty value: giving the page back to the detection is a request to
                        detect it, which writes the page.
    :ivar included: New decision whether the page is part of the book.
    :ivar notes: New notes of the user, or an empty string for none.
    :ivar group_label: New label of the group of the page, or an empty string for no group.
    """

    label: str | None = None
    kind: PageKind | None = None
    content_type: ContentType | None = None
    included: bool | None = None
    notes: str | None = None
    group_label: str | None = None

    def apply_to(self, page: Page) -> Page:
        """Return ``page`` with every given field replaced.

        The update time is left to the caller, which owns the clock.

        :param page: Current state of the page.
        :type page: Page
        :returns: The page with the given fields replaced and the others kept.
        :rtype: Page
        """
        given = {name: value for name, value in asdict(self, recurse=False).items() if value is not None}
        if self.label is not None:
            given['label_manual'] = bool(self.label)
        # A leaf stands in place of the scan of a blank page only
        if self.kind is not None and self.kind is not PageKind.BLANK:
            given['blank_fill'] = BlankFill.SCAN
        if self.content_type is not None:
            given['content_by_hand'] = True
        return evolve(page, **given)
