"""The addresses of the images of a scan or a page, as paths of the IIIF route without scheme or host.

An address is built from the storage key of the file with the name of the route that serves it, so no URL is written by
hand. It has no scheme or host because the browser resolves it against the origin it loaded the page from, and an
address kept by a client or written into a pyramid then survives a change of domain, port or device address.

The ``full`` image is a JPEG or a PNG, and its address names the format recorded with the scan or the page version when
the image was written, never one worked out from the project's image policy as it is now, since a later change of the
policy does not change the files already stored.
"""

from typing import TYPE_CHECKING, Self

from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.base import ResponseModel
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE
from bookreviver.domain.enums import Rendition
from bookreviver.domain.keys import ProjectKeys

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

    from bookreviver.domain.ids import StorageKey


class ImagePathsSchema(ResponseModel):
    """The images a scan or a page has once they are cut, each as the path of a file of the IIIF route.

    :ivar full: The image at its native resolution.
    :ivar preview: The image shrunk to 2048 pixels on its longer side.
    :ivar thumbnail: The image shrunk to a thumbnail.
    :ivar iiif_info: The image information document of the tile pyramid, which a viewer reads to find the tiles.
    """

    full: str
    preview: str
    thumbnail: str
    iiif_info: str

    @classmethod
    def of(cls, request: Request, key_of: Callable[[Rendition], StorageKey], *, full: Rendition) -> Self:
        """Build the paths of the four images of a scan or a page version.

        :param request: The request, whose application knows the IIIF route.
        :type request: Request
        :param key_of: Function giving the storage key of one rendition of the scan or the page version.
        :type key_of: Callable[[Rendition], StorageKey]
        :param full: Format the full image was written in, as recorded with the scan or the page version.
        :type full: Rendition
        :returns: The paths of the full image, the preview, the thumbnail and the pyramid's information document.
        :rtype: Self
        """

        def path(key: str) -> str:
            """Return the path of the IIIF route serving a file.

            :param key: Storage key of the file.
            :type key: str
            :returns: The path of the file below the API prefix, without scheme or host.
            :rtype: str
            """
            return str(request.app.url_path_for(RouteName.IIIF_FILE, key=key))

        info = f'{key_of(Rendition.TILES)}{ProjectKeys.SEPARATOR}{IIIF_INFO_FILE}'
        return cls(
            full=path(key_of(full)),
            preview=path(key_of(Rendition.PREVIEW)),
            thumbnail=path(key_of(Rendition.THUMBNAIL)),
            iiif_info=path(info),
        )
