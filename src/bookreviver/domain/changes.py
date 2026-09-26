"""Partial changes of value objects: a field left as None keeps its current value."""

from typing import TYPE_CHECKING

from attrs import asdict, evolve, frozen

if TYPE_CHECKING:
    from bookreviver.domain.enums import Orthography
    from bookreviver.domain.values import BookDetails


@frozen(kw_only=True)
class BookDetailsChanges:
    """New values for some fields of a book description."""

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
        """Return ``details`` with every given field replaced, checked like a new description."""
        given = {name: value for name, value in asdict(self, recurse=False).items() if value is not None}
        return evolve(details, **given)
