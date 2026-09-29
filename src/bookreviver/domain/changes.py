"""Partial changes of value objects, as an edit form or a JSON Merge Patch sends them.

A change names only the fields to replace, and a field left as None keeps its current value. Clearing a field is a
change to its empty value, such as an empty string, so the domain needs no third state between keeping and
replacing. The API turns a field sent as null into that empty value.
"""

from typing import TYPE_CHECKING

from attrs import asdict, evolve, frozen

if TYPE_CHECKING:
    from bookreviver.domain.enums import Orthography
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
