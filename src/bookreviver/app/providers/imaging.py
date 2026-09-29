"""Provider of the imaging adapters: source inspector, page rasterizer and tiler.

The adapters keep no per-request state, so one instance of each serves the whole application. The rasterizer and the
tiler read their sizes and quality from ``Settings.imaging``, and the inspector needs no settings.
"""

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
        """Build the rasterizer encoding rendered pages at the configured JPEG quality.

        :param settings: Application settings, of which ``imaging.jpeg_quality`` is read.
        :type settings: Settings
        :returns: The PyMuPDF and Pillow rasterizer.
        :rtype: PageRasterizer
        """
        return PdfImagePageRasterizer(jpeg_quality=settings.imaging.jpeg_quality)

    @provide
    def tiler(self, settings: Settings) -> Tiler:
        """Build the tiler with the configured tile size, thumbnail size and JPEG quality.

        :param settings: Application settings, of which the ``imaging`` group is read.
        :type settings: Settings
        :returns: The libvips tiler.
        :rtype: Tiler
        """
        imaging = settings.imaging
        return VipsTiler(
            tile_size_px=imaging.tile_size_px,
            thumbnail_long_side_px=imaging.thumbnail_long_side_px,
            jpeg_quality=imaging.jpeg_quality,
        )
