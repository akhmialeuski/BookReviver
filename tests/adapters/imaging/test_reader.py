"""Tests for the source reader grouping an upload into sources and dispatching each source to its format."""

from typing import TYPE_CHECKING, NamedTuple

import pytest

from bookreviver.adapters.imaging import DjvuFormat, ImageFormat, PdfFormat, SourceReader
from bookreviver.domain.enums import FileType, Rendition, SourceKind, UploadProblem
from bookreviver.domain.errors import UnsupportedSourceError, UploadRejectedError
from bookreviver.domain.values import UploadedSource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.adapters.imaging import SourceFormat
    from bookreviver.ports.imaging import PageRasterizer, SourceInspector

pytestmark = pytest.mark.anyio

CASE_ARG: str = 'case'
DJVU_NAME: str = 'book.djvu'
TARGET_NAME: str = 'full.jpg'
DJVU_REFUSAL_MATCH: str = r'^Reading DjVu files is not set up on this server.*djvulibre package'
DJVU_TIMEOUT_S: int = 120
REGISTRY_MATCH: str = r'^Register exactly one source format per kind'
JPEG_QUALITY: int = 90


class GroupCase(NamedTuple):
    """An upload, and the sources it must be grouped into.

    :ivar names: Names of the staged files, in upload order.
    :ivar sources: Sources the upload must make, in book order.
    """

    names: Sequence[str]
    sources: Sequence[UploadedSource]


class RejectedGroupCase(NamedTuple):
    """An upload that breaks an upload rule, and the problem it must be refused with.

    :ivar names: Names of the staged files, in upload order.
    :ivar problem: Upload problem the refusal must carry.
    """

    names: Sequence[str]
    problem: UploadProblem


def _source(kind: SourceKind, file_type: FileType, *names: str) -> UploadedSource:
    """Return an expected source made of the named files.

    :param kind: Kind of the source.
    :type kind: SourceKind
    :param file_type: Type of the main file.
    :type file_type: FileType
    :param names: Names of the files, the main file first.
    :type names: str
    :returns: The source as the reader must group it.
    :rtype: UploadedSource
    """
    return UploadedSource(kind=kind, file_type=file_type, names=list(names))


class TestSourceReaderInit:
    """Tests for SourceReader.__init__()."""

    @pytest.mark.parametrize(
        'formats',
        [
            (PdfFormat(jpeg_quality=JPEG_QUALITY), ImageFormat(jpeg_quality=JPEG_QUALITY)),
            (
                PdfFormat(jpeg_quality=JPEG_QUALITY),
                ImageFormat(jpeg_quality=JPEG_QUALITY),
                DjvuFormat(tools=None, jpeg_quality=JPEG_QUALITY, timeout_s=DJVU_TIMEOUT_S),
                DjvuFormat(tools=None, jpeg_quality=JPEG_QUALITY, timeout_s=DJVU_TIMEOUT_S),
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


class TestGroup:
    """Tests for SourceInspector.group()."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            GroupCase(names=['book.pdf'], sources=[_source(SourceKind.PDF, FileType.PDF, 'book.pdf')]),
            # Each part of a book is a source of its own, and only the natural order puts part 2 before part 10
            GroupCase(
                names=['part10.pdf', 'part2.PDF'],
                sources=[
                    _source(SourceKind.PDF, FileType.PDF, 'part2.PDF'),
                    _source(SourceKind.PDF, FileType.PDF, 'part10.pdf'),
                ],
            ),
            # A cover from another copy, a PDF part and a multi-page TIFF are accepted together
            GroupCase(
                names=['scans.tif', 'kniga.pdf', 'cover.JPG'],
                sources=[
                    _source(SourceKind.IMAGE, FileType.JPEG, 'cover.JPG'),
                    _source(SourceKind.PDF, FileType.PDF, 'kniga.pdf'),
                    _source(SourceKind.IMAGE, FileType.TIFF, 'scans.tif'),
                ],
            ),
            GroupCase(
                names=['002.png', '001.jp2', DJVU_NAME],
                sources=[
                    _source(SourceKind.IMAGE, FileType.JPEG_2000, '001.jp2'),
                    _source(SourceKind.IMAGE, FileType.PNG, '002.png'),
                    _source(SourceKind.DJVU, FileType.DJVU, DJVU_NAME),
                ],
            ),
        ],
        ids=['one-pdf', 'pdf-parts', 'mixed-kinds', 'image-files-and-djvu'],
    )
    async def test_makes_one_source_per_file_in_natural_order(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: GroupCase
    ) -> None:
        """Verify every file is a source of its own kind and type, ordered by the natural order of the names.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: An upload, and the sources it must be grouped into.
        :type case: GroupCase
        """
        sources = await fx_inspector.group([tmp_path / name for name in case.names])

        assert list(sources) == list(case.sources)

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            RejectedGroupCase(names=[], problem=UploadProblem.NO_FILES),
            RejectedGroupCase(names=['page.tif', 'Thumbs.db'], problem=UploadProblem.UNSUPPORTED_TYPE),
            RejectedGroupCase(names=['no-suffix'], problem=UploadProblem.UNSUPPORTED_TYPE),
        ],
        ids=['empty', 'unsupported', 'no-suffix'],
    )
    async def test_rejects_upload_breaking_a_rule(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: RejectedGroupCase
    ) -> None:
        """Reject an empty upload and an upload holding a file of a type that is not accepted.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: An upload that breaks an upload rule, and the problem it must be refused with.
        :type case: RejectedGroupCase
        """
        with pytest.raises(UploadRejectedError) as error:
            await fx_inspector.group([tmp_path / name for name in case.names])

        assert error.value.problem is case.problem


@pytest.fixture
def fx_reader_without_djvulibre() -> SourceReader:
    """Return a reader whose DjVu format was given no DjVuLibre tools, as on a server that lacks the package.

    :returns: The reader with every kind of source registered.
    :rtype: SourceReader
    """
    return SourceReader(
        formats=(
            PdfFormat(jpeg_quality=JPEG_QUALITY),
            ImageFormat(jpeg_quality=JPEG_QUALITY),
            DjvuFormat(tools=None, jpeg_quality=JPEG_QUALITY, timeout_s=DJVU_TIMEOUT_S),
        )
    )


class TestInspectDjvu:
    """Tests for SourceInspector.inspect() of a DjVu source when the DjVuLibre tools are not installed."""

    async def test_refuses_djvu_naming_the_missing_package(
        self, fx_reader_without_djvulibre: SourceReader, tmp_path: Path
    ) -> None:
        """Refuse a DjVu source with a message that asks to install the djvulibre package.

        :param fx_reader_without_djvulibre: Reader whose DjVu format has no tools.
        :type fx_reader_without_djvulibre: SourceReader
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / DJVU_NAME
        path.write_bytes(b'')

        with pytest.raises(UnsupportedSourceError, match=DJVU_REFUSAL_MATCH):
            await fx_reader_without_djvulibre.inspect(SourceKind.DJVU, [path])


class TestExtractDjvu:
    """Tests for PageRasterizer.extract() of a DjVu scan when the DjVuLibre tools are not installed."""

    async def test_refuses_djvu_naming_the_missing_package(
        self, fx_reader_without_djvulibre: SourceReader, tmp_path: Path
    ) -> None:
        """Refuse a DjVu scan that asks to install the djvulibre package, and write nothing.

        :param fx_reader_without_djvulibre: Reader whose DjVu format has no tools.
        :type fx_reader_without_djvulibre: SourceReader
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / DJVU_NAME
        path.write_bytes(b'')
        target = tmp_path / TARGET_NAME

        with pytest.raises(UnsupportedSourceError, match=DJVU_REFUSAL_MATCH):
            await fx_reader_without_djvulibre.extract(SourceKind.DJVU, [path], 0, target, full=Rendition.FULL_JPEG)
        assert not target.exists()
