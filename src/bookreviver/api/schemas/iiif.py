"""The IIIF Image API documents the viewer reads next to the tiles of a page."""

from pydantic import ConfigDict

from bookreviver.api.schemas.base import ResponseModel

# Name of the image information document at the root of a tile pyramid
IIIF_INFO_FILE: str = 'info.json'
IIIF_INFO_MEDIA_TYPE: str = 'application/ld+json;profile="http://iiif.io/api/image/3/context.json"'


class IiifImageInfo(ResponseModel):
    """An IIIF Image API 3 ``info.json``; every property besides ``id`` passes through unchanged."""

    model_config = ConfigDict(extra='allow')

    id: str
