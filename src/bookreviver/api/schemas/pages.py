"""Schemas of pages: technical facts, readiness and the addresses of their images."""

from typing import TYPE_CHECKING, Any, Self

from pydantic import HttpUrl

from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.base import ResponseModel
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE
from bookreviver.domain.enums import ColorMode, PageAsset

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.entities import Page


class PageFactsSchema(ResponseModel):
    """Technical facts of one source page."""

    width_px: int
    height_px: int
    color_mode: ColorMode
    dpi_x: float | None
    dpi_y: float | None
    bits_per_component: int | None
    image_format: str
    width_mm: float | None
    height_mm: float | None
    has_text_layer: bool
    source_file: str
    extra: dict[str, Any]


class PageSchema(ResponseModel):
    """One page of a book; the image addresses are set once its images are ready."""

    index: int
    facts: PageFactsSchema
    ready: bool
    info_url: HttpUrl | None
    thumbnail_url: HttpUrl | None

    @classmethod
    def from_page(cls, page: Page, request: Request) -> Self:
        """Build the schema of a page with absolute addresses of its IIIF information document and thumbnail."""
        ready = page.assets.ready
        info_key = f'{page.asset_key(PageAsset.TILES)}/{IIIF_INFO_FILE}'
        thumbnail_key = page.asset_key(PageAsset.THUMBNAIL)
        return cls(
            index=page.index,
            facts=PageFactsSchema.model_validate(page.facts),
            ready=ready,
            info_url=HttpUrl(str(request.url_for(RouteName.IIIF_FILE, key=info_key))) if ready else None,
            thumbnail_url=HttpUrl(str(request.url_for(RouteName.IIIF_FILE, key=thumbnail_key))) if ready else None,
        )
