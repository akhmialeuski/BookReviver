"""Schemas of projects: the book description sent by clients and the project resource returned to them.

``ProjectUpdate`` is a JSON Merge Patch (RFC 7396) of the description and of the project's own settings: a field left
out keeps its value, and a field sent as null is cleared to the value a new project has for it, an empty string, an
empty list, no height, an unknown orthography, script or rights status, the compact image policy or no cover page. A
list is replaced as a whole, because a merge patch cannot change an array item by item.
The title can be replaced but never cleared, so it is left out of the null variants of the OpenAPI schema and a null
title is refused. Pydantic declares no field that may be omitted but not null outside its experimental ``MISSING``
sentinel, which mypy cannot check yet, so a field validator states that one rule.

The rule of an identifier lives in the domain, where the suggestions read from files meet it too, and
``IdentifierSchema`` only calls it, so a wrong ISBN is refused with a validation error that names the field. The
contributors and identifiers are nested schemas, so their list types are declared here beside them and not with the
other constrained types.

The project resource describes the book and counts it: its included pages, its sources and their scans. The sources
themselves are resources of their own, so the project carries no field naming a source.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Self

import attrs
from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.stages import StageProgressSchema
from bookreviver.api.schemas.types import (
    CONTRIBUTORS_MAX_LENGTH,
    IDENTIFIERS_MAX_LENGTH,
    HeightCm,
    IdentifierValue,
    LanguageList,
    LongText,
    PersonName,
    ShortText,
    SubjectList,
    Title,
    TitleList,
)
from bookreviver.domain.changes import BookDetailsChanges, CoverChange, ProjectChanges
from bookreviver.domain.entities import Project
from bookreviver.domain.enums import (
    ContributorRole,
    IdentifierScheme,
    ImagePolicy,
    Orthography,
    RightsStatus,
    Script,
    Stage,
)
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.domain.values import BookDetails, BookIdentifier, Contributor

if TYPE_CHECKING:
    from bookreviver.domain.entities import ProjectOverview

TITLE_FIELD: str = 'title'
COVER_FIELD: str = 'cover_page_id'
IMAGE_POLICY_FIELD: str = 'image_policy'
TITLE_NOT_CLEARABLE: str = 'The title can be changed but not cleared.'


class ContributorSchema(RequestModel):
    """A person who took part in the making of a book, with the role they had.

    :ivar name: Name as printed in the book.
    :ivar role: Role of the person, a code of the MARC list of relators.
    """

    model_config = ConfigDict(from_attributes=True)

    name: PersonName
    role: ContributorRole


class IdentifierSchema(RequestModel):
    """A number or an address that identifies a book or a copy of it.

    :ivar scheme: Kind of the identifier.
    :ivar value: The identifier in any spelling its scheme accepts, which the resource shows normalized.
    """

    model_config = ConfigDict(from_attributes=True)

    scheme: IdentifierScheme
    value: IdentifierValue

    @model_validator(mode='after')
    def _follows_its_scheme(self) -> Self:
        """Check the value against the rules of the scheme, so a wrong ISBN is a validation error of the field.

        :returns: The schema unchanged.
        :rtype: Self
        :raises InvalidIdentifierError: If the value breaks the rules of the scheme, which Pydantic reports as a
            validation error because the error is a ``ValueError``.
        """
        self.scheme.normalize(self.value)
        return self


ContributorList = Annotated[list[ContributorSchema], Field(max_length=CONTRIBUTORS_MAX_LENGTH)]
IdentifierList = Annotated[list[IdentifierSchema], Field(max_length=IDENTIFIERS_MAX_LENGTH)]


def _domain_value(value: object) -> object:
    """Turn a value of a description schema into the value the domain holds.

    :param value: A field of the description as the schema holds it.
    :type value: object
    :returns: A tuple for a list, a domain object for a nested schema, and any other value unchanged.
    :rtype: object
    """
    match value:
        case list():
            return tuple(_domain_value(item) for item in value)
        case ContributorSchema():
            return Contributor(name=value.name, role=value.role)
        case IdentifierSchema():
            return BookIdentifier.parse(value.scheme, value.value)
        case _:
            return value


class ProjectCreate(RequestModel):
    """The description of a new book; only the title is required.

    :ivar title: Title of the book.
    :ivar subtitle: Words of the title page that explain the title.
    :ivar parallel_titles: Titles in other languages that the title page prints beside the title.
    :ivar original_title: Title of the original work, for a translation.
    :ivar contributors: People who made the book, each with a role, in the order of the title page.
    :ivar publisher: Publisher of the book.
    :ivar printer: Printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be a range or an estimate.
    :ivar edition: Edition statement.
    :ivar censorship: Censor's permit printed in the book.
    :ivar series: Series the book belongs to.
    :ivar series_number: Number of the book in its series.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar languages: ISO 639-3 codes of the languages of the text.
    :ivar orthography: Spelling system of the print.
    :ivar script: Writing system of the print.
    :ivar printed_pagination: Pagination as a catalogue states it.
    :ivar height_cm: Height of the book in centimetres.
    :ivar illustrations: Illustrations as a catalogue states them.
    :ivar binding: Binding or cover of the copy.
    :ivar identifiers: Numbers and addresses that identify the book or the copy.
    :ivar subjects: Topics of the book.
    :ivar rights: Whether the result of the work may be published.
    :ivar copy_holder: Owner of the copy that was scanned.
    :ivar copy_notes: Marks of the copy that was scanned.
    :ivar notes: Free-form notes of the owner.
    """

    title: Title
    subtitle: ShortText = ''
    parallel_titles: TitleList = Field(default_factory=list)
    original_title: ShortText = ''
    contributors: ContributorList = Field(default_factory=list)
    publisher: ShortText = ''
    printer: ShortText = ''
    publication_place: ShortText = ''
    publication_year: ShortText = ''
    edition: ShortText = ''
    censorship: ShortText = ''
    series: ShortText = ''
    series_number: ShortText = ''
    volume: ShortText = ''
    languages: LanguageList = Field(default_factory=list)
    orthography: Orthography = Orthography.UNKNOWN
    script: Script = Script.UNKNOWN
    printed_pagination: ShortText = ''
    height_cm: HeightCm | None = None
    illustrations: ShortText = ''
    binding: ShortText = ''
    identifiers: IdentifierList = Field(default_factory=list)
    subjects: SubjectList = Field(default_factory=list)
    rights: RightsStatus = RightsStatus.UNKNOWN
    copy_holder: ShortText = ''
    copy_notes: LongText = ''
    notes: LongText = ''

    def to_details(self) -> BookDetails:
        """Return the description as a domain value.

        :returns: The description with every field the client sent or its default.
        :rtype: BookDetails
        :raises InvalidIdentifierError: If an identifier breaks the rules of its scheme, which the schema has checked.
        """
        values: dict[str, Any] = {name: _domain_value(getattr(self, name)) for name in type(self).model_fields}
        return BookDetails(**values)


class ProjectUpdate(RequestModel):
    """A JSON Merge Patch of a book description: an omitted field is kept and a null one is cleared.

    :ivar title: New title, which may be omitted but not null.
    :ivar subtitle: New subtitle, or None to clear it.
    :ivar parallel_titles: New titles in other languages, replacing the list, or None to empty it.
    :ivar original_title: New title of the original work, or None to clear it.
    :ivar contributors: New people who made the book, replacing the list, or None to empty it.
    :ivar publisher: New publisher, or None to clear it.
    :ivar printer: New printing house, or None to clear it.
    :ivar publication_place: New city of publication, or None to clear it.
    :ivar publication_year: New year of publication, or None to clear it.
    :ivar edition: New edition statement, or None to clear it.
    :ivar censorship: New censor's permit, or None to clear it.
    :ivar series: New series, or None to clear it.
    :ivar series_number: New number in the series, or None to clear it.
    :ivar volume: New volume, or None to clear it.
    :ivar languages: New ISO 639-3 codes, replacing the list, or None to empty it.
    :ivar orthography: New orthography, or None to set it to unknown.
    :ivar script: New writing system, or None to set it to unknown.
    :ivar printed_pagination: New pagination, or None to clear it.
    :ivar height_cm: New height in centimetres, or None to clear it.
    :ivar illustrations: New statement of the illustrations, or None to clear it.
    :ivar binding: New binding, or None to clear it.
    :ivar identifiers: New identifiers, replacing the list, or None to empty it.
    :ivar subjects: New topics, replacing the list, or None to empty it.
    :ivar rights: New publishing rights, or None to set them to unknown.
    :ivar copy_holder: New owner of the scanned copy, or None to clear it.
    :ivar copy_notes: New marks of the scanned copy, or None to clear them.
    :ivar notes: New notes, or None to clear them.
    :ivar image_policy: New way to store the images of new scans and page versions, or None to set the default.
    :ivar cover_page_id: New cover page, which must be a page of the project, or None to fall back to the first page.
    """

    title: Title | SkipJsonSchema[None] = None
    subtitle: ShortText | None = None
    parallel_titles: TitleList | None = None
    original_title: ShortText | None = None
    contributors: ContributorList | None = None
    publisher: ShortText | None = None
    printer: ShortText | None = None
    publication_place: ShortText | None = None
    publication_year: ShortText | None = None
    edition: ShortText | None = None
    censorship: ShortText | None = None
    series: ShortText | None = None
    series_number: ShortText | None = None
    volume: ShortText | None = None
    languages: LanguageList | None = None
    orthography: Orthography | None = None
    script: Script | None = None
    printed_pagination: ShortText | None = None
    height_cm: HeightCm | None = None
    illustrations: ShortText | None = None
    binding: ShortText | None = None
    identifiers: IdentifierList | None = None
    subjects: SubjectList | None = None
    rights: RightsStatus | None = None
    copy_holder: ShortText | None = None
    copy_notes: LongText | None = None
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
        :raises InvalidIdentifierError: If an identifier breaks the rules of its scheme, which the schema has checked.
        """
        sent = {name: getattr(self, name) for name in self.model_fields_set}
        cover = CoverChange(page_id=sent.pop(COVER_FIELD)) if COVER_FIELD in sent else None
        image_policy = None
        if IMAGE_POLICY_FIELD in sent:
            image_policy = sent.pop(IMAGE_POLICY_FIELD) or Project.DEFAULT_IMAGE_POLICY
        empty = attrs.fields_dict(BookDetails)
        details: dict[str, Any] = {
            name: empty[name].default if value is None else _domain_value(value) for name, value in sent.items()
        }
        return ProjectChanges(details=BookDetailsChanges(**details), image_policy=image_policy, cover=cover)


class BookDetailsSchema(ResponseModel):
    """Bibliographic description of a book.

    :ivar title: Title of the book.
    :ivar subtitle: Words of the title page that explain the title.
    :ivar parallel_titles: Titles in other languages that the title page prints beside the title.
    :ivar original_title: Title of the original work, for a translation.
    :ivar contributors: People who made the book, each with a role, in the order of the title page.
    :ivar primary_author: The first author, else the first contributor of any role, else an empty string.
    :ivar publisher: Publisher of the book.
    :ivar printer: Printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed.
    :ivar edition: Edition statement.
    :ivar censorship: Censor's permit printed in the book.
    :ivar series: Series the book belongs to.
    :ivar series_number: Number of the book in its series.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar languages: ISO 639-3 codes of the languages of the text.
    :ivar orthography: Spelling system of the print.
    :ivar script: Writing system of the print.
    :ivar printed_pagination: Pagination as a catalogue states it.
    :ivar height_cm: Height of the book in centimetres, or None when unknown.
    :ivar illustrations: Illustrations as a catalogue states them.
    :ivar binding: Binding or cover of the copy.
    :ivar identifiers: Numbers and addresses that identify the book or the copy, in normalized form.
    :ivar subjects: Topics of the book.
    :ivar rights: Whether the result of the work may be published.
    :ivar copy_holder: Owner of the copy that was scanned.
    :ivar copy_notes: Marks of the copy that was scanned.
    :ivar notes: Free-form notes of the owner.
    """

    title: str
    subtitle: str
    parallel_titles: list[str]
    original_title: str
    contributors: list[ContributorSchema]
    primary_author: str
    publisher: str
    printer: str
    publication_place: str
    publication_year: str
    edition: str
    censorship: str
    series: str
    series_number: str
    volume: str
    languages: list[str]
    orthography: Orthography
    script: Script
    printed_pagination: str
    height_cm: int | None
    illustrations: str
    binding: str
    identifiers: list[IdentifierSchema]
    subjects: list[str]
    rights: RightsStatus
    copy_holder: str
    copy_notes: str
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
    :ivar progress: The status of every stage of the book, in the order of the pipeline.
    :ivar next_stage: The first available stage with work to do or being worked, or None when there is none.
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
    progress: list[StageProgressSchema]
    next_stage: Stage | None
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
        progress = overview.progress
        return cls(
            id=project.id,
            details=BookDetailsSchema.model_validate(project.details),
            image_policy=project.image_policy,
            cover_page_id=project.cover_page_id,
            page_count=overview.page_count,
            source_count=overview.source_count,
            scan_count=overview.scan_count,
            progress=[] if progress is None else [StageProgressSchema.model_validate(one) for one in progress.stages],
            next_stage=None if progress is None else progress.next_stage,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
