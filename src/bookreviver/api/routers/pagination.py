"""The pagination sections of a book: runs of pages that are numbered by one rule, from the page each one starts at.

A section lasts until the next section of its flow starts, and a series by kind takes the pages of its kinds across the
book, so the plates of a book can have their own numbers. The printed number of a page follows the sections, except a
number written by hand, which stays. Every change of a section writes the numbers that change in its own transaction,
and the pages renumbered reach the browser as a ``book-changed`` event.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page, Params

from bookreviver.api.access import open_book
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.pagination import PaginationSectionBody, PaginationSectionSchema
from bookreviver.domain.entities import PaginationSection
from bookreviver.domain.ids import PaginationSectionId
from bookreviver.services.book import Book
from bookreviver.services.pagination import PaginationService

router = APIRouter(prefix='/books', tags=['pagination'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@dataclass(frozen=True)
class SectionPath:
    """The identifiers in the address of one pagination section of a book.

    :ivar section_id: Identifier of the section.
    """

    section_id: Annotated[PaginationSectionId, Path(description='Identifier of the pagination section')]


@router.get('/{book_id}/pagination-sections')
async def list_pagination_sections(
    book: Annotated[Book, Depends(open_book)],
    params: Annotated[Params, Depends()],
    pagination: FromDishka[PaginationService],
) -> Page[PaginationSectionSchema]:
    """List the pagination sections of a book in book order, which is the order of the pages they start at.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param params: Page number and size from the query.
    :type params: Params
    :param pagination: Pagination service of the request.
    :type pagination: PaginationService
    :returns: One page of the sections of the book.
    :rtype: Page[PaginationSectionSchema]
    """
    pager = Pager[PaginationSection, PaginationSectionSchema](params, PaginationSectionSchema.of)
    return pager.page(await pagination.sections(book, pager.request))


@router.post('/{book_id}/pagination-sections', status_code=status.HTTP_201_CREATED)
async def create_pagination_section(
    book: Annotated[Book, Depends(open_book)],
    body: PaginationSectionBody,
    pagination: FromDishka[PaginationService],
) -> PaginationSectionSchema:
    """Make a section that starts at a page, and renumber the pages it takes.

    The answer is 404 when the page is not a page of the book, and 409 when another section that takes the same
    pages starts at that page already, or when a number does not fit the style, such as 4000 in Roman numerals.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param body: The first page, the name, the style, the first number, the prefix, the display and the kinds.
    :type body: PaginationSectionBody
    :param pagination: Pagination service of the request.
    :type pagination: PaginationService
    :returns: The section as stored.
    :rtype: PaginationSectionSchema
    """
    return PaginationSectionSchema.of(await pagination.add(book, body.to_draft()))


@router.put('/{book_id}/pagination-sections/{section_id}')
async def put_pagination_section(
    book: Annotated[Book, Depends(open_book)],
    address: Annotated[SectionPath, Depends()],
    body: PaginationSectionBody,
    pagination: FromDishka[PaginationService],
) -> PaginationSectionSchema:
    """Replace the first page and the rule of a section, and renumber the pages.

    The answer is 404 when the section or the page is not of the book, and 409 when another section that takes the
    same pages starts at that page, or when a number does not fit the style.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param address: Identifiers of the book and the section.
    :type address: SectionPath
    :param body: The first page, the name, the style, the first number, the prefix, the display and the kinds.
    :type body: PaginationSectionBody
    :param pagination: Pagination service of the request.
    :type pagination: PaginationService
    :returns: The section as stored.
    :rtype: PaginationSectionSchema
    """
    section = await pagination.update(book, address.section_id, body.to_draft())
    return PaginationSectionSchema.of(section)


@router.delete('/{book_id}/pagination-sections/{section_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_pagination_section(
    book: Annotated[Book, Depends(open_book)],
    address: Annotated[SectionPath, Depends()],
    pagination: FromDishka[PaginationService],
) -> None:
    """Remove a section, so its pages fall to the section before it, and renumber them.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param address: Identifiers of the book and the section.
    :type address: SectionPath
    :param pagination: Pagination service of the request.
    :type pagination: PaginationService
    """
    await pagination.remove(book, address.section_id)
