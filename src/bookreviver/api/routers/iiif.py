"""The derived files of a book as immutable static files: renditions of scans and pages, and IIIF tile pyramids.

Every file is served exactly as it is stored but a pyramid's ``info.json``, whose ``id`` the route sets to the path the
document was asked at, without its file name: the address of the pyramid, which the viewer builds the address of every
tile from. The stored document never names its book, so a book imported under a new identifier, or served at another
domain, port or device address, keeps every pyramid it has.

The key of a file carries the version of its renditions, so the content behind a key never changes and browsers cache
it for good. No account and no owner is checked: anyone who has the key of a file reads it, and the key holds the
identifier of its book, which only the owner is told. On a server a reverse proxy can take over ``/iiif``, since all
files are served alike.
"""

import json
import pathlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

import anyio
from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import FileResponse, Response

from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE, IIIF_INFO_MEDIA_TYPE
from bookreviver.api.schemas.types import AssetKey
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.services.pages import PageService

# Where the router is mounted below the API prefix, which a pyramid's ``info.json`` names as its address
IIIF_PREFIX: str = '/iiif'
# Asset keys carry the version of the asset, so the content behind a URL never changes, and no shared cache keeps it
IMMUTABLE_CACHE: dict[str, str] = {'Cache-Control': 'private, max-age=31536000, immutable'}
KEY_DESCRIPTION: str = 'Storage key of the file, as found in the image addresses of a page or a scan'
# The property of an image information document that holds the address of its image service
IIIF_ID: str = 'id'

router = APIRouter(prefix=IIIF_PREFIX, tags=['iiif'], route_class=DishkaRoute)


@inject
async def opened_asset(
    key: Annotated[AssetKey, Path(description=KEY_DESCRIPTION)], pages: FromDishka[PageService]
) -> AsyncIterator[pathlib.Path]:
    """Keep the stored file at ``key`` open until the response carrying it has been sent.

    :param key: Storage key of the file.
    :type key: AssetKey
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: Iterator yielding once the path of the file, and closing the file afterwards.
    :rtype: AsyncIterator[pathlib.Path]
    :raises NotFoundError: If the key is not under the ``assets/`` of a book, no file is stored there, or the key
                           names a directory.
    """
    # try/finally rather than a context manager around the yield, because FastAPI drives this generator
    opened = AsyncExitStack()
    try:
        path = await opened.enter_async_context(pages.open_asset(StorageKey(key)))
        if not await anyio.Path(path).is_file():
            raise NotFoundError(key)
        yield path
    finally:
        await opened.aclose()


@router.get('/{key:path}', name=RouteName.IIIF_FILE, response_class=FileResponse)
async def iiif_file(
    key: Annotated[AssetKey, Path(description=KEY_DESCRIPTION)],
    path: Annotated[pathlib.Path, Depends(opened_asset)],
    request: Request,
) -> Response:
    """Stream a stored file, and an image information document with its IIIF media type and the address of its pyramid.

    \N{FORM FEED}
    :param key: Storage key of the file.
    :type key: AssetKey
    :param path: Local path of the file, kept open until the response has been sent.
    :type path: pathlib.Path
    :param request: The request, whose path, without the name of the document, is the address of the pyramid.
    :type request: Request
    :returns: The file, cacheable for good.
    :rtype: Response
    """
    if pathlib.PurePosixPath(key).name != IIIF_INFO_FILE:
        return FileResponse(path, headers=IMMUTABLE_CACHE)
    info = json.loads(await anyio.Path(path).read_bytes())
    info[IIIF_ID] = request.url.path.removesuffix(f'/{IIIF_INFO_FILE}')
    return Response(json.dumps(info), media_type=IIIF_INFO_MEDIA_TYPE, headers=IMMUTABLE_CACHE)
