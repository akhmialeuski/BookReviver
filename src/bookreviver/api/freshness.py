"""Conditional reads of a book: a read that still holds is answered 304 before its handler runs.

Every read of a book answers with a weak ``ETag`` made of the mark of the book, its version and the mark of its jobs,
and of a digest of the path and the query asked for, and with ``Cache-Control: private, no-cache``, so the browser
keeps the answer and asks again with ``If-None-Match`` every time. When the tag it sends is still the tag of the book,
the read is answered 304 without a body and its handler never runs. The routers of one book take :func:`fresh_book` as
a dependency of every route, and it lets anything but a read of a book through. It takes the same open book as the
route, so the owner is checked once for both.

A dependency cannot answer a request itself, so a read that still holds raises :class:`NotModifiedError`, which
:func:`not_modified` answers. fastapi-problem answers every ``HTTPException`` with a problem body, which a 304 must not
carry, so the 304 has an exception and a handler of its own. The headers a dependency sets on the response reach only a
route that returns data, which every read of a book does.
"""

import hashlib
from http import HTTPMethod, HTTPStatus
from typing import TYPE_CHECKING, Annotated, Final

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Request, Response

from bookreviver.api.access import open_book
from bookreviver.services.book import Book
from bookreviver.services.freshness import BookFreshness

if TYPE_CHECKING:
    from collections.abc import Mapping

ETAG_HEADER: Final = 'ETag'
IF_NONE_MATCH_HEADER: Final = 'If-None-Match'
CACHE_CONTROL_HEADER: Final = 'Cache-Control'
# The browser keeps the answer for this account alone and asks again before it uses it
REVALIDATE: Final = 'private, no-cache'
WEAK_PREFIX: Final = 'W/'
ANY_TAG: Final = '*'
TAG_SEPARATOR: Final = ','
# Bytes of the digest of the path and the query, enough to keep the tags of two reads of one book apart
DIGEST_BYTES: Final = 8


class NotModifiedError(Exception):
    """A read of a book that still holds, which is answered 304 with the headers of the read.

    :ivar headers: The tag and the caching of the read.
    """

    def __init__(self, headers: Mapping[str, str]) -> None:
        """Keep the headers the 304 carries.

        :param headers: The tag and the caching of the read.
        :type headers: Mapping[str, str]
        """
        super().__init__(HTTPStatus.NOT_MODIFIED.phrase)
        self.headers = dict(headers)


def _matches(sent: str | None, tag: str) -> bool:
    """Tell whether ``If-None-Match`` names the tag, compared weakly as RFC 9110 has it for ``If-None-Match``.

    :param sent: The header the client sent, or None.
    :type sent: str | None
    :param tag: The tag of the read now.
    :type tag: str
    :returns: True when the header names the tag or any tag.
    :rtype: bool
    """
    if sent is None:
        return False
    named = {part.strip().removeprefix(WEAK_PREFIX) for part in sent.split(TAG_SEPARATOR)}
    return ANY_TAG in named or tag.removeprefix(WEAK_PREFIX) in named


@inject
async def fresh_book(
    request: Request,
    response: Response,
    book: Annotated[Book, Depends(open_book)],
    freshness: FromDishka[BookFreshness],
) -> None:
    """Tag a read of a book, and answer it 304 when the client holds it as it still is.

    :param request: The request, whose method, path, query and ``If-None-Match`` are read.
    :type request: Request
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param response: The response of a read that runs, which receives the tag and the caching.
    :type response: Response
    :param freshness: Freshness of the reads of the books of the request.
    :type freshness: BookFreshness
    :raises NotModifiedError: If the client holds the read as it still is.
    """
    if request.method != HTTPMethod.GET:
        return
    asked = request.url.path if not request.url.query else f'{request.url.path}?{request.url.query}'
    digest = hashlib.blake2b(asked.encode(), digest_size=DIGEST_BYTES).hexdigest()
    tag = f'{WEAK_PREFIX}"{await freshness.mark(book)}-{digest}"'
    headers = {ETAG_HEADER: tag, CACHE_CONTROL_HEADER: REVALIDATE}
    if _matches(request.headers.get(IF_NONE_MATCH_HEADER), tag):
        raise NotModifiedError(headers)
    response.headers.update(headers)


def not_modified(_request: Request, error: Exception) -> Response:
    """Answer a read that still holds with 304, its tag and its caching, and no body.

    :param _request: The request, which the answer does not need.
    :type _request: Request
    :param error: The :class:`NotModifiedError` the dependency raised.
    :type error: Exception
    :returns: The empty answer.
    :rtype: Response
    """
    headers = error.headers if isinstance(error, NotModifiedError) else {}
    return Response(status_code=HTTPStatus.NOT_MODIFIED, headers=headers)
