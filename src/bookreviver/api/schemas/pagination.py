"""Schemas of the pagination sections of a book, which number its pages from the page each section starts at.

A section is replaced as a whole, so the body that makes one is the body that changes one. A section without kinds
belongs to the main flow of the book, and a section with kinds is a series by kind, such as the plates of the book.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Self

from pydantic import Field, model_validator

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import NumberPrefix, PageKindList, SectionName
from bookreviver.domain.enums import LabelStyle, NumberDisplay, PageKind
from bookreviver.domain.ids import PageId, PaginationSectionId, ProjectId
from bookreviver.domain.values import PaginationSectionDraft

if TYPE_CHECKING:
    from bookreviver.domain.entities import PaginationSection


class PaginationSectionSchema(ResponseModel):
    """A pagination section of a book.

    :ivar id: Identifier of the section.
    :ivar project_id: Project owning the section.
    :ivar first_page_id: The page the section starts at, so the section follows the page when it is moved.
    :ivar name: Name the user sees.
    :ivar style: How the numbers of the section are written.
    :ivar start: Number of the first counted page of the section.
    :ivar prefix: Text written before every number, empty for none.
    :ivar display: Whether the pages count, and whether their numbers are printed or only implied.
    :ivar kinds: Kinds of page the section takes as a series by kind, empty for a section of the main flow.
    :ivar created_at: When the section was made.
    :ivar updated_at: When the section was last changed.
    """

    id: PaginationSectionId
    project_id: ProjectId
    first_page_id: PageId
    name: str
    style: LabelStyle
    start: int
    prefix: str
    display: NumberDisplay
    kinds: list[PageKind]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, section: PaginationSection) -> Self:
        """Build the schema of a section, with its kinds in the order of the page kinds.

        :param section: The section.
        :type section: PaginationSection
        :returns: The section resource.
        :rtype: Self
        """
        return cls(
            id=section.id,
            project_id=section.project_id,
            first_page_id=section.first_page_id,
            name=section.name,
            style=section.style,
            start=section.start,
            prefix=section.prefix,
            display=section.display,
            kinds=[kind for kind in PageKind if kind in section.kinds],
            created_at=section.created_at,
            updated_at=section.updated_at,
        )


class PaginationSectionBody(RequestModel):
    """A section to make, or the whole new state of a section that exists.

    :ivar first_page_id: The page the section starts at, which must be a page of the project.
    :ivar name: Name the user sees.
    :ivar style: How the numbers are written.
    :ivar start: Number of the first counted page, from 1, and at most 3999 in a Roman style.
    :ivar prefix: Text written before every number, such as ``Plate ``; it keeps its spaces.
    :ivar display: Whether the pages count, and whether their numbers are printed or only implied.
    :ivar kinds: Kinds of page a series by kind takes, each at most once; leave out for a section of the main flow.
    """

    first_page_id: PageId
    name: SectionName = ''
    style: LabelStyle
    start: Annotated[int, Field(ge=1)] = 1
    prefix: NumberPrefix = ''
    display: NumberDisplay = NumberDisplay.PRINTED
    kinds: PageKindList = Field(default_factory=list)

    @model_validator(mode='after')
    def _first_number_fits_the_style(self) -> Self:
        """Check that the first number can be written in the style.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)
        return self

    def to_draft(self) -> PaginationSectionDraft:
        """Return the body as the domain states it.

        :returns: The draft of the section.
        :rtype: PaginationSectionDraft
        """
        return PaginationSectionDraft(
            first_page_id=self.first_page_id,
            name=self.name,
            style=self.style,
            start=self.start,
            prefix=self.prefix,
            display=self.display,
            kinds=frozenset(self.kinds),
        )
