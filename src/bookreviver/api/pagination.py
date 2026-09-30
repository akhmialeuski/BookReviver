"""Conversion between fastapi-pagination's page parameters and the domain's slices."""

from typing import TYPE_CHECKING, TypeVar

from fastapi import Query
from fastapi_pagination import Page, Params
from fastapi_pagination.customization import CustomizedPage, UseName, UseParams
from pydantic import BaseModel

from bookreviver.domain.values import SliceRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.values import Slice

PageItemT = TypeVar('PageItemT')
MANIFEST_DEFAULT_SIZE: int = 50
# A viewer needs the whole book in few requests, so a manifest page may hold ten times the 100 pages Params allows
MANIFEST_MAX_SIZE: int = 1_000
# Name of the page class in the OpenAPI schema, which the generated client takes its type name from
MANIFEST_PAGE_NAME: str = 'ManifestPage'


class ManifestParams(Params):
    """Page parameters of a book's page manifest, which may ask for up to a thousand pages at once.

    :ivar page: Number of the page of the manifest, from one.
    :ivar size: Number of pages of the book in one page of the manifest.
    """

    size: int = Query(MANIFEST_DEFAULT_SIZE, ge=1, le=MANIFEST_MAX_SIZE, description='Page size')


# fastapi-pagination checks the query against the parameters of the response's page class as well as against those of
# the route, so the limit has to be raised on the page class too, or a window of more than 100 pages is refused
ManifestPage = CustomizedPage[Page[PageItemT], UseParams(ManifestParams), UseName(MANIFEST_PAGE_NAME)]


class Pager[ItemT, SchemaT: BaseModel]:
    """Turns page parameters into a slice request, and a slice into a typed page of schemas."""

    def __init__(self, params: Params, to_schema: Callable[[ItemT], SchemaT]) -> None:
        """Page one collection with the request's page parameters.

        :param params: Page number and size fastapi-pagination parsed from the query.
        :type params: Params
        :param to_schema: Function mapping one domain object to its response schema.
        :type to_schema: Callable[[ItemT], SchemaT]
        """
        self._params = params
        self._to_schema = to_schema

    @property
    def request(self) -> SliceRequest:
        """The slice of the collection the page parameters ask for."""
        raw = self._params.to_raw_params().as_limit_offset()
        return SliceRequest(offset=raw.offset or 0, limit=raw.limit or self._params.size)

    def page(self, result: Slice[ItemT]) -> Page[SchemaT]:
        """Wrap a slice of domain objects as a page of response schemas.

        :param result: Domain objects of the requested window and the size of the whole collection.
        :type result: Slice[ItemT]
        :returns: The page with its items, total, page number, size and page count.
        :rtype: Page[SchemaT]
        """
        return Page[SchemaT].create([self._to_schema(item) for item in result.items], self._params, total=result.total)
