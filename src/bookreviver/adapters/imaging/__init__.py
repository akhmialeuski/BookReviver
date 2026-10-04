"""Imaging adapters: one reader dispatching to a format per kind of source, and libvips cutting tiles."""

from bookreviver.adapters.imaging.djvu import DjvuFormat, DjvuLibreTools
from bookreviver.adapters.imaging.epub_page_list import EpubPageList
from bookreviver.adapters.imaging.images import ImageFormat
from bookreviver.adapters.imaging.memory_labels import MemoryPageLabelWriter
from bookreviver.adapters.imaging.pdf import PdfFormat
from bookreviver.adapters.imaging.pdf_labels import PdfLabelWriter
from bookreviver.adapters.imaging.reader import SourceFormat, SourceReader
from bookreviver.adapters.imaging.renditions import VipsRenditionWriter
from bookreviver.adapters.imaging.tiler import VipsTiler

__all__ = [
    'DjvuFormat',
    'DjvuLibreTools',
    'EpubPageList',
    'ImageFormat',
    'MemoryPageLabelWriter',
    'PdfFormat',
    'PdfLabelWriter',
    'SourceFormat',
    'SourceReader',
    'VipsRenditionWriter',
    'VipsTiler',
]
