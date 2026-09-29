"""Conversion between fastapi-pagination's page parameters and the domain's slices."""

from typing import TYPE_CHECKING

from fastapi_pagination import Page
from pydantic import BaseModel

from bookreviver.domain.values import SliceRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi_pagination import Params

    from bookreviver.domain.values import Slice


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
