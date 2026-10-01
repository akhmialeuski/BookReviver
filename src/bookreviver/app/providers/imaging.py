"""Provider of the imaging adapters: the source reader with its formats, the tiler and the rendition writer.

The adapters keep no per-request state, so one instance of each serves the whole application. One ``SourceReader``
serves as both the source inspector and the page rasterizer, and this provider is where its formats are registered:
supporting another kind of source adds its format to the list in ``source_reader``. The formats and the tiler read
their sizes and quality from ``Settings.imaging``.
"""

import logging

from dishka import AnyOf, Provider, Scope, provide

from bookreviver.adapters.imaging import (
    DjvuFormat,
    DjvuLibreTools,
    ImageFormat,
    PdfFormat,
    SourceReader,
    VipsBlankPageMaker,
    VipsRenditionWriter,
    VipsTiler,
)
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import BlankPageMaker, PageRasterizer, RenditionWriter, SourceInspector, Tiler

logger = logging.getLogger(__name__)


class ImagingProvider(Provider):
    """Builds the imaging adapters, one of each per application, configured by the imaging settings."""

    scope = Scope.APP

    @provide(provides=AnyOf[SourceInspector, PageRasterizer])
    def source_reader(self, settings: Settings) -> SourceReader:
        """Build the reader with one format per kind of source, encoding scans at the configured JPEG quality.

        :param settings: Application settings, of which ``imaging.jpeg_quality`` is read.
        :type settings: Settings
        :returns: The reader dispatching to the PDF, image and DjVu formats.
        :rtype: SourceReader
        """
        jpeg_quality = settings.imaging.jpeg_quality
        djvu_tools = DjvuLibreTools.locate()
        if djvu_tools is None:
            logger.warning(
                'The DjVuLibre tools djvused, djvudump and ddjvu are not installed, so every DjVu source will be '
                'refused. Install the djvulibre package to read DjVu.'
            )
        djvu = DjvuFormat(tools=djvu_tools, jpeg_quality=jpeg_quality, timeout_s=settings.imaging.djvulibre_timeout_s)
        return SourceReader(
            formats=(PdfFormat(jpeg_quality=jpeg_quality), ImageFormat(jpeg_quality=jpeg_quality), djvu)
        )

    @provide
    def blank_page_maker(self) -> BlankPageMaker:
        """Build the maker of blank leaves, a temporary adapter that the ``pages.blank`` processor will replace.

        :returns: The libvips blank page maker.
        :rtype: BlankPageMaker
        """
        return VipsBlankPageMaker()

    @provide
    def rendition_writer(self, settings: Settings) -> RenditionWriter:
        """Build the writer of the files of a page version with the configured preview and thumbnail sizes.

        :param settings: Application settings, of which the ``imaging`` group is read.
        :type settings: Settings
        :returns: The libvips rendition writer.
        :rtype: RenditionWriter
        """
        imaging = settings.imaging
        return VipsRenditionWriter(
            preview_long_side_px=imaging.preview_long_side_px,
            thumbnail_long_side_px=imaging.thumbnail_long_side_px,
            jpeg_quality=imaging.jpeg_quality,
        )

    @provide
    def tiler(self, settings: Settings) -> Tiler:
        """Build the tiler with the configured tile, preview and thumbnail sizes and JPEG quality.

        :param settings: Application settings, of which the ``imaging`` group is read.
        :type settings: Settings
        :returns: The libvips tiler.
        :rtype: Tiler
        """
        imaging = settings.imaging
        return VipsTiler(
            tile_size_px=imaging.tile_size_px,
            preview_long_side_px=imaging.preview_long_side_px,
            thumbnail_long_side_px=imaging.thumbnail_long_side_px,
            jpeg_quality=imaging.jpeg_quality,
        )
