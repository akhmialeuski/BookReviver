"""Provider of the imaging adapters: source inspector, page rasterizer and tiler."""

from dishka import Provider, Scope, provide

from bookreviver.adapters.imaging import PdfImagePageRasterizer, PdfImageSourceInspector, VipsTiler
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler


class ImagingProvider(Provider):
    """Builds the imaging adapters, one of each per application, configured by the imaging settings."""

    scope = Scope.APP

    inspector = provide(PdfImageSourceInspector, provides=SourceInspector)

    @provide
    def rasterizer(self, settings: Settings) -> PageRasterizer:
        """Build the rasterizer encoding rendered pages at the configured JPEG quality."""
        return PdfImagePageRasterizer(jpeg_quality=settings.imaging.jpeg_quality)

    @provide
    def tiler(self, settings: Settings) -> Tiler:
        """Build the tiler with the configured tile size, thumbnail size and JPEG quality."""
        imaging = settings.imaging
        return VipsTiler(
            tile_size_px=imaging.tile_size_px,
            thumbnail_long_side_px=imaging.thumbnail_long_side_px,
            jpeg_quality=imaging.jpeg_quality,
        )
