"""Schemas of projects: the book description sent by clients and the project resource returned to them.

``ProjectUpdate`` is a JSON Merge Patch (RFC 7396) of the description and of the project's own settings: a field left
out keeps its value, and a field sent as null is cleared to the value a new project has for it, an empty string, an
unknown orthography, the compact image policy or no cover page.
The title can be replaced but never cleared, so it is left out of the null variants of the OpenAPI schema and a null
title is refused. Pydantic declares no field that may be omitted but not null outside its experimental ``MISSING``
sentinel, which mypy cannot check yet, so a field validator states that one rule.

The project resource describes the book and counts it: its included pages, its sources and their scans. The sources
themselves are resources of their own, so the project carries no field naming a source.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

import attrs
from pydantic import field_validator
from pydantic.json_schema import SkipJsonSchema

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import LongText, ShortText, Title
from bookreviver.domain.changes import BookDetailsChanges, CoverChange, ProjectChanges
from bookreviver.domain.entities import Project
from bookreviver.domain.enums import ImagePolicy, Orthography
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.domain.values import BookDetails

if TYPE_CHECKING:
    from bookreviver.domain.entities import ProjectOverview

TITLE_FIELD: str = 'title'
COVER_FIELD: str = 'cover_page_id'
IMAGE_POLICY_FIELD: str = 'image_policy'
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
    :ivar image_policy: New way to store the images of new scans and page versions, or None to set the default.
    :ivar cover_page_id: New cover page, which must be a page of the project, or None to fall back to the first page.
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
    image_policy: ImagePolicy | None = None
    cover_page_id: PageId | None = None

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

    def to_changes(self) -> ProjectChanges:
        """Return the fields the client sent as a domain change, a null field changed to its empty value.

        :returns: The change replacing exactly the fields present in the request body.
        :rtype: ProjectChanges
        """
        sent = self.model_dump(exclude_unset=True)
        cover = CoverChange(page_id=sent.pop(COVER_FIELD)) if COVER_FIELD in sent else None
        image_policy = None
        if IMAGE_POLICY_FIELD in sent:
            image_policy = sent.pop(IMAGE_POLICY_FIELD) or Project.DEFAULT_IMAGE_POLICY
        empty = attrs.fields_dict(BookDetails)
        details: dict[str, Any] = {
            name: empty[name].default if value is None else value for name, value in sent.items()
        }
        return ProjectChanges(details=BookDetailsChanges(**details), image_policy=image_policy, cover=cover)


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
    :ivar image_policy: How the images of the project's scans and page versions are stored.
    :ivar cover_page_id: Page whose thumbnail the project list shows, or None for the first page of the book.
    :ivar page_count: Number of pages of the book, without the pages kept out of it.
    :ivar source_count: Number of sources the book was assembled from.
    :ivar scan_count: Number of scans in all the sources.
    :ivar created_at: When the project was created.
    :ivar updated_at: When the project was last changed.
    """

    id: ProjectId
    details: BookDetailsSchema
    image_policy: ImagePolicy
    cover_page_id: PageId | None
    page_count: int
    source_count: int
    scan_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_overview(cls, overview: ProjectOverview) -> Self:
        """Build the schema of a project and the counts of its book.

        :param overview: The project with the counts of its book.
        :type overview: ProjectOverview
        :returns: The project resource.
        :rtype: Self
        """
        project = overview.project
        return cls(
            id=project.id,
            details=BookDetailsSchema.model_validate(project.details),
            image_policy=project.image_policy,
            cover_page_id=project.cover_page_id,
            page_count=overview.page_count,
            source_count=overview.source_count,
            scan_count=overview.scan_count,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
