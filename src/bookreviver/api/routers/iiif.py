"""The derived files of a book as immutable static files: renditions of scans and pages, and IIIF tile pyramids.

Every file is served exactly as it is stored. A pyramid's ``info.json`` already names the path of this route as its
``id``, because the tiler was given that path when it cut the pyramid, so the route neither parses the document nor
builds an address from the request. A change of domain, port or device address therefore leaves every cut pyramid valid.

The key of a file carries the version of its renditions, so the content behind a key never changes and browsers cache
it for good. The cache is private because access is checked for every request. On a server a reverse proxy can take
over ``/iiif`` after the API has checked access, since all files are served alike.
"""

import pathlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

import anyio
from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from bookreviver.api.auth import ActorDep
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE, IIIF_INFO_MEDIA_TYPE
from bookreviver.api.schemas.types import AssetKey
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.services.pages import PageService

# Where the router is mounted below the API prefix, which a pyramid's ``info.json`` names as its address
IIIF_PREFIX: str = '/iiif'
# Asset keys carry the version of the asset, so the content behind a URL never changes; private, as access is checked
IMMUTABLE_CACHE: dict[str, str] = {'Cache-Control': 'private, max-age=31536000, immutable'}
KEY_DESCRIPTION: str = 'Storage key of the file, as found in the image addresses of a page or a scan'

router = APIRouter(prefix=IIIF_PREFIX, tags=['iiif'], route_class=DishkaRoute)


@inject
async def opened_asset(
    key: Annotated[AssetKey, Path(description=KEY_DESCRIPTION)], actor: ActorDep, pages: FromDishka[PageService]
) -> AsyncIterator[pathlib.Path]:
    """Keep the stored file at ``key`` open until the response carrying it has been sent.

    :param key: Storage key of the file.
    :type key: AssetKey
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: Iterator yielding once the path of the file, and closing the file afterwards.
    :rtype: AsyncIterator[pathlib.Path]
    :raises NotFoundError: If the key is not under the ``assets/`` of one of the actor's projects, no file is stored
                           there, or the key names a directory.
    """
    # try/finally rather than a context manager around the yield, because FastAPI drives this generator
    opened = AsyncExitStack()
    try:
        path = await opened.enter_async_context(pages.open_asset(actor, StorageKey(key)))
        if not await anyio.Path(path).is_file():
            raise NotFoundError(key)
        yield path
    finally:
        await opened.aclose()


@router.get('/{key:path}', name=RouteName.IIIF_FILE, response_class=FileResponse)
async def iiif_file(
    key: Annotated[AssetKey, Path(description=KEY_DESCRIPTION)],
    path: Annotated[pathlib.Path, Depends(opened_asset)],
) -> FileResponse:
    """Stream a stored file exactly as it was written, an image information document with its IIIF media type.

    \N{FORM FEED}
    :param key: Storage key of the file.
    :type key: AssetKey
    :param path: Local path of the file, kept open until the response has been sent.
    :type path: pathlib.Path
    :returns: The file, cacheable for good.
    :rtype: FileResponse
    """
    is_info = pathlib.PurePosixPath(key).name == IIIF_INFO_FILE
    return FileResponse(path, media_type=IIIF_INFO_MEDIA_TYPE if is_info else None, headers=IMMUTABLE_CACHE)
