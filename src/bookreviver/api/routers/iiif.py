"""IIIF tile pyramids and page images as immutable files."""

import pathlib
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

import anyio
from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import FileResponse, Response

from bookreviver.api.auth import ActorDep
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE, IIIF_INFO_MEDIA_TYPE, IiifImageInfo
from bookreviver.api.schemas.types import AssetKey
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.services.pages import PageService

# Asset keys carry the asset version, so the content behind a URL never changes; private because access is checked
IMMUTABLE_CACHE: dict[str, str] = {'Cache-Control': 'private, max-age=31536000, immutable'}

# Parameter and dependency aliases; FastAPI and dishka read route annotations at runtime
AssetKeyPath = Annotated[AssetKey, Path(description='Storage key of the file, as found in the addresses of a page')]
PageServiceDep = FromDishka[PageService]
AssetPaths = AsyncIterator[pathlib.Path]

router = APIRouter(prefix='/iiif', tags=['iiif'], route_class=DishkaRoute)


@inject
async def opened_asset(key: AssetKeyPath, actor: ActorDep, pages: PageServiceDep) -> AssetPaths:
    """Keep the stored file at ``key`` open until the response carrying it has been sent.

    :raises NotFoundError: If the key is not in one of the actor's projects, or no file is stored there.
    """
    # try/finally rather than `async with` around the yield: FastAPI drives this generator (ASYNC119)
    opened = AsyncExitStack()
    try:
        path = await opened.enter_async_context(pages.open_asset(actor, StorageKey(key)))
        if not await anyio.Path(path).is_file():
            raise NotFoundError(key)
        yield path
    finally:
        await opened.aclose()


OpenedAssetDep = Annotated[pathlib.Path, Depends(opened_asset)]


@router.get('/{key:path}', name=RouteName.IIIF_FILE, response_class=FileResponse)
async def iiif_file(key: AssetKeyPath, request: Request, path: OpenedAssetDep) -> Response:
    """Stream a tile, a thumbnail or a page image; an ``info.json`` gets the public address of its pyramid as ``id``."""
    key_path = pathlib.PurePosixPath(key)
    if key_path.name != IIIF_INFO_FILE:
        return FileResponse(path, headers=IMMUTABLE_CACHE)
    info = IiifImageInfo.model_validate_json(await anyio.Path(path).read_bytes())
    public_id = str(request.url_for(RouteName.IIIF_FILE, key=str(key_path.parent)))
    content = info.model_copy(update={'id': public_id}).model_dump_json()
    return Response(content, media_type=IIIF_INFO_MEDIA_TYPE, headers=IMMUTABLE_CACHE)
