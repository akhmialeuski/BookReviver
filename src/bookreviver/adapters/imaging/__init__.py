"""Imaging adapters: one reader dispatching to a format per kind of source, and libvips cutting tiles."""

from bookreviver.adapters.imaging.djvu import DjvuFormat
from bookreviver.adapters.imaging.images import ImageSetFormat
from bookreviver.adapters.imaging.pdf import PdfFormat
from bookreviver.adapters.imaging.reader import SourceFormat, SourceReader
from bookreviver.adapters.imaging.tiler import VipsTiler

__all__ = ['DjvuFormat', 'ImageSetFormat', 'PdfFormat', 'SourceFormat', 'SourceReader', 'VipsTiler']
