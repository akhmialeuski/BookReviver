"""Schemas of projects: the book description sent by clients and the project resource returned to them.

``ProjectUpdate`` is a JSON Merge Patch (RFC 7396) of the description: a field left out keeps its value, and a field
sent as null is cleared to the empty value a new description has for it, an empty string or an unknown orthography.
The title can be replaced but never cleared, so it is left out of the null variants of the OpenAPI schema and a null
title is refused. Pydantic declares no field that may be omitted but not null outside its experimental ``MISSING``
sentinel, which mypy cannot check yet, so a field validator states that one rule.

The project resource carries no source summary. The import model is being replaced by a book with any number of
sources, and the schema gains its source fields together with that model, so the generated frontend client never
sees a field that is about to change.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

import attrs
from pydantic import field_validator
from pydantic.json_schema import SkipJsonSchema

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import LongText, ShortText, Title
from bookreviver.domain.changes import BookDetailsChanges
from bookreviver.domain.enums import Orthography
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.values import BookDetails

if TYPE_CHECKING:
    from bookreviver.domain.entities import ProjectOverview

TITLE_FIELD: str = 'title'
TITLE_NOT_CLEARABLE: str = 'The title can be changed but not cleared.'


class ProjectCreate(RequestModel):
    """The description of a new book; only the title is required.

    :ivar title: Title of the book.
    :ivar authors: Authors as printed, in one string.
    :ivar publisher: Publisher or printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be a range or an estimate.
    :ivar edition: Edition statement.
    :ivar series: Series the book belongs to.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar language: Language of the text.
    :ivar orthography: Spelling system of the print.
    :ivar notes: Free-form notes of the owner.
    """

    title: Title
    authors: ShortText = ''
    publisher: ShortText = ''
    publication_place: ShortText = ''
    publication_year: ShortText = ''
    edition: ShortText = ''
    series: ShortText = ''
    volume: ShortText = ''
    language: ShortText = ''
    orthography: Orthography = Orthography.UNKNOWN
    notes: LongText = ''

    def to_details(self) -> BookDetails:
        """Return the description as a domain value.

        :returns: The description with every field the client sent or its default.
        :rtype: BookDetails
        """
        return BookDetails(**self.model_dump())


class ProjectUpdate(RequestModel):
    """A JSON Merge Patch of a book description: an omitted field is kept and a null one is cleared.

    :ivar title: New title, which may be omitted but not null.
    :ivar authors: New authors, or None to clear them.
    :ivar publisher: New publisher, or None to clear it.
    :ivar publication_place: New city of publication, or None to clear it.
    :ivar publication_year: New year of publication, or None to clear it.
    :ivar edition: New edition statement, or None to clear it.
    :ivar series: New series, or None to clear it.
    :ivar volume: New volume, or None to clear it.
    :ivar language: New language, or None to clear it.
    :ivar orthography: New orthography, or None to set it to unknown.
    :ivar notes: New notes, or None to clear them.
    """

    title: Title | SkipJsonSchema[None] = None
    authors: ShortText | None = None
    publisher: ShortText | None = None
    publication_place: ShortText | None = None
    publication_year: ShortText | None = None
    edition: ShortText | None = None
    series: ShortText | None = None
    volume: ShortText | None = None
    language: ShortText | None = None
    orthography: Orthography | None = None
    notes: LongText | None = None

    @field_validator(TITLE_FIELD)
    @classmethod
    def _title_is_not_cleared(cls, title: str | None) -> str:
        """Refuse a title sent as null; an omitted title keeps its default and never reaches this check.

        :param title: The title the client sent.
        :type title: str | None
        :returns: The title unchanged.
        :rtype: str
        :raises ValueError: If the client sent null.
        """
        if title is None:
            raise ValueError(TITLE_NOT_CLEARABLE)
        return title

    def to_changes(self) -> BookDetailsChanges:
        """Return the fields the client sent as a domain change, a null field changed to its empty value.

        :returns: The change replacing exactly the fields present in the request body.
        :rtype: BookDetailsChanges
        """
        empty = attrs.fields_dict(BookDetails)
        sent = self.model_dump(exclude_unset=True)
        changes: dict[str, Any] = {
            name: empty[name].default if value is None else value for name, value in sent.items()
        }
        return BookDetailsChanges(**changes)


class BookDetailsSchema(ResponseModel):
    """Bibliographic description of a book.

    :ivar title: Title of the book.
    :ivar authors: Authors as printed, in one string.
    :ivar publisher: Publisher or printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed.
    :ivar edition: Edition statement.
    :ivar series: Series the book belongs to.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar language: Language of the text.
    :ivar orthography: Spelling system of the print.
    :ivar notes: Free-form notes of the owner.
    """

    title: str
    authors: str
    publisher: str
    publication_place: str
    publication_year: str
    edition: str
    series: str
    volume: str
    language: str
    orthography: Orthography
    notes: str


class ProjectSchema(ResponseModel):
    """A book being digitised.

    :ivar id: Identifier of the project.
    :ivar details: Bibliographic description of the book.
    :ivar page_count: Number of pages imported into the project.
    :ivar created_at: When the project was created.
    :ivar updated_at: When the project was last changed.
    """

    id: ProjectId
    details: BookDetailsSchema
    page_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_overview(cls, overview: ProjectOverview) -> Self:
        """Build the schema of a project and the size of its book.

        :param overview: The project with the number of its pages.
        :type overview: ProjectOverview
        :returns: The project resource.
        :rtype: Self
        """
        project = overview.project
        return cls(
            id=project.id,
            details=BookDetailsSchema.model_validate(project.details),
            page_count=overview.page_count,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
