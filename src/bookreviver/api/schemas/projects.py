"""Schemas of projects: the book description sent by clients and the project resource returned to them."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import LongText, ShortText, Title
from bookreviver.domain.changes import BookDetailsChanges
from bookreviver.domain.enums import Orthography, SourceKind
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.values import BookDetails

if TYPE_CHECKING:
    from bookreviver.domain.entities import ProjectOverview


class ProjectCreate(RequestModel):
    """The description of a new book; only the title is required."""

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
        """Return the description as a domain value."""
        return BookDetails(**self.model_dump())


class ProjectUpdate(RequestModel):
    """New values for some fields of a book description; an omitted or null field keeps its value."""

    title: Title | None = None
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

    def to_changes(self) -> BookDetailsChanges:
        """Return the fields the client sent as a domain change."""
        return BookDetailsChanges(**self.model_dump(exclude_unset=True))


class BookDetailsSchema(ResponseModel):
    """Bibliographic description of a book."""

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


class SourceSchema(ResponseModel):
    """The file or files a book was imported from."""

    kind: SourceKind
    name: str
    size_bytes: int
    metadata: dict[str, Any]
    imported_at: datetime


class ProjectSchema(ResponseModel):
    """A book being digitised."""

    id: ProjectId
    details: BookDetailsSchema
    source: SourceSchema | None
    page_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_overview(cls, overview: ProjectOverview) -> Self:
        """Build the schema of a project and the size of its book."""
        project = overview.project
        return cls(
            id=project.id,
            details=BookDetailsSchema.model_validate(project.details),
            source=SourceSchema.model_validate(project.source) if project.source else None,
            page_count=overview.page_count,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
