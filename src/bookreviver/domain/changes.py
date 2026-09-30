"""Partial changes of value objects, as an edit form or a JSON Merge Patch sends them.

A change names only the fields to replace, and a field left as None keeps its current value. Clearing a field is a
change to its empty value, such as an empty string, so the domain needs no third state between keeping and
replacing. The API turns a field sent as null into that empty value. The one field with no empty value of its own is
the cover page of a project, where None is the empty value, so its change is the object ``CoverChange``.
"""

from typing import TYPE_CHECKING

from attrs import asdict, evolve, field, frozen

if TYPE_CHECKING:
    from bookreviver.domain.entities import Project
    from bookreviver.domain.enums import ImagePolicy, Orthography
    from bookreviver.domain.ids import PageId
    from bookreviver.domain.values import BookDetails


@frozen(kw_only=True)
class BookDetailsChanges:
    """New values for some fields of a book description; None keeps the current value.

    :ivar title: New title, which may not be empty.
    :ivar authors: New authors as printed.
    :ivar publisher: New publisher or printing house.
    :ivar publication_place: New city of publication.
    :ivar publication_year: New year of publication as printed.
    :ivar edition: New edition statement.
    :ivar series: New series the book belongs to.
    :ivar volume: New volume or part within a multi-volume work.
    :ivar language: New language of the text.
    :ivar orthography: New spelling system of the print.
    :ivar notes: New free-form notes of the owner.
    """

    title: str | None = None
    authors: str | None = None
    publisher: str | None = None
    publication_place: str | None = None
    publication_year: str | None = None
    edition: str | None = None
    series: str | None = None
    volume: str | None = None
    language: str | None = None
    orthography: Orthography | None = None
    notes: str | None = None

    def apply_to(self, details: BookDetails) -> BookDetails:
        """Return ``details`` with every given field replaced, checked like a new description.

        :param details: Current description of the book.
        :type details: BookDetails
        :returns: The description with the given fields replaced and the others kept.
        :rtype: BookDetails
        :raises ValueError: If the result breaks a rule of a description, such as an empty title.
        """
        given = {name: value for name, value in asdict(self, recurse=False).items() if value is not None}
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
