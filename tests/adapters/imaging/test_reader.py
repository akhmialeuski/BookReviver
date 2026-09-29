"""Tests for the source reader dispatching each kind of source to its format."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.imaging import DjvuFormat, ImageSetFormat, PdfFormat, SourceReader
from bookreviver.domain.enums import SourceKind
from bookreviver.domain.errors import UnsupportedSourceError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.adapters.imaging import SourceFormat
    from bookreviver.ports.imaging import PageRasterizer, SourceInspector

pytestmark = pytest.mark.anyio

DJVU_NAME: str = 'book.djvu'
TARGET_NAME: str = 'full.jpg'
DJVU_REFUSAL_MATCH: str = r'^Importing DjVu files is not supported yet'
REGISTRY_MATCH: str = r'^Register exactly one source format per kind'
JPEG_QUALITY: int = 90


class TestSourceReaderInit:
    """Tests for SourceReader.__init__()."""

    @pytest.mark.parametrize(
        'formats',
        [
            (PdfFormat(jpeg_quality=JPEG_QUALITY), ImageSetFormat(jpeg_quality=JPEG_QUALITY)),
            (
                PdfFormat(jpeg_quality=JPEG_QUALITY),
                ImageSetFormat(jpeg_quality=JPEG_QUALITY),
                DjvuFormat(),
                DjvuFormat(),
            ),
        ],
        ids=['kind-missing', 'kind-twice'],
    )
    def test_rejects_registry_not_covering_each_kind_once(self, formats: Sequence[SourceFormat]) -> None:
        """Reject a registry that leaves a kind without a format or gives a kind two formats.

        :param formats: Formats that do not cover every kind of source exactly once.
        :type formats: Sequence[SourceFormat]
        """
        with pytest.raises(ValueError, match=REGISTRY_MATCH):
            SourceReader(formats=formats)


class TestSourceReaderProvider:
    """Tests for the reader the imaging provider builds."""

    async def test_serves_both_ports_with_one_reader(
        self, fx_inspector: SourceInspector, fx_rasterizer: PageRasterizer
    ) -> None:
        """Verify the inspector and the rasterizer are one reader, so both use the same formats.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        """
        assert isinstance(fx_inspector, SourceReader)
        assert fx_inspector is fx_rasterizer


class TestInspectDjvu:
    """Tests for SourceInspector.inspect() of a DjVu source, served by DjvuFormat."""

    async def test_refuses_djvu_until_supported(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Refuse a DjVu source with a message saying the format is not supported yet.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / DJVU_NAME
        path.write_bytes(b'')

        with pytest.raises(UnsupportedSourceError, match=DJVU_REFUSAL_MATCH):
            await fx_inspector.inspect(SourceKind.DJVU, [path])


class TestExtractDjvu:
    """Tests for PageRasterizer.extract() of a DjVu page, served by DjvuFormat."""

    async def test_refuses_djvu_until_supported(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Refuse a DjVu page and write nothing.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / DJVU_NAME
        path.write_bytes(b'')
        target = tmp_path / TARGET_NAME

        with pytest.raises(UnsupportedSourceError, match=DJVU_REFUSAL_MATCH):
            await fx_rasterizer.extract(SourceKind.DJVU, [path], 0, target)
        assert not target.exists()
