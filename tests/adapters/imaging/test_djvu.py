"""Tests for reading DjVu sources with the DjVuLibre tools: the facts of every page, and each page written as an image.

A page is written as a JPEG or a PNG as the caller asks, and a bilevel page written as a PNG is a 1-bit PNG.
"""

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from attrs import evolve
from PIL import Image

from bookreviver.adapters.imaging import DjvuFormat, DjvuLibreTools
from bookreviver.adapters.imaging.common import FactKey, to_mm
from bookreviver.adapters.imaging.djvu import HEADER_LAYOUT
from bookreviver.domain.enums import ColorMode, ContributorRole, DjvuDocumentKind, FileType, Rendition, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import Contributor, ScanFacts, UploadedSource
from bookreviver.ports.imaging import SourceInspector
from tests.adapters.imaging.samples import (
    DjvuPage,
    edit_djvu,
    requires_djvulibre,
    write_djvu_bundle,
    write_djvu_including,
    write_djvu_indirect,
    write_djvu_pages,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import AsyncContainer

    from bookreviver.ports.imaging import PageRasterizer

pytestmark = pytest.mark.anyio

RUN_TARGET: str = 'bookreviver.adapters.imaging.djvu.subprocess.run'
LOCATE_TARGET: str = 'bookreviver.adapters.imaging.djvu.DjvuLibreTools.locate'
TIMEOUT_S: int = 7
JPEG_QUALITY: int = 90
JPEG_FORMAT: str = 'JPEG'
TARGET_NAME: str = 'full.jpg'
INDEX_NAME: str = 'index.djvu'
# Pages that differ in size, resolution and colour mode, so a page mistaken for another shows in its facts
PAGES: tuple[DjvuPage, ...] = (
    DjvuPage(size_px=(300, 400), dpi=300, mode=ColorMode.COLOR),
    DjvuPage(size_px=(250, 350), dpi=200, mode=ColorMode.GRAY),
    DjvuPage(size_px=(320, 420), dpi=600, mode=ColorMode.BILEVEL),
)
# The mode of the JPEG each colour mode is written as: a JPEG holds no single bit, and gray pages stay gray
JPEG_MODES: dict[ColorMode, str] = {ColorMode.COLOR: 'RGB', ColorMode.GRAY: 'L', ColorMode.BILEVEL: 'L'}
# The mode of the PNG each colour mode is written as: a PNG keeps the single bit of a bilevel page
PNG_MODES: dict[ColorMode, str] = {ColorMode.COLOR: 'RGB', ColorMode.GRAY: 'L', ColorMode.BILEVEL: '1'}
PNG_FORMAT: str = 'PNG'
PNG_TARGET_NAME: str = 'full.png'
BILEVEL_CHUNKS: list[str] = ['INFO', 'Sjbz']
COLOR_CHUNK: str = 'BG44'
KINDS: list[DjvuDocumentKind] = list(DjvuDocumentKind)
CYRILLIC_TITLE: str = 'Букварь для народных школ'
CYRILLIC_AUTHOR: str = 'Иван Петров'
NOT_DJVU_MATCH: str = r'^page\.djvu is not a DjVu file'
DAMAGED_MATCH: str = r'^page\.djvu cannot be read as DjVu: the file is damaged'
TIMEOUT_MATCH: str = r'^page\.djvu took too long to read as DjVu'
MISSING_MATCH: str = (
    r'^index\.djvu is an indirect DjVu document, and 2 of its files are missing.*p0002\.djvu, p0003\.djvu'
)


def _expected(page: DjvuPage) -> ScanFacts:
    """Return the facts a page sample must be reported with, but for its chunk identifiers.

    :param page: The sample page.
    :type page: DjvuPage
    :returns: The size, resolution, physical size, colour mode and bit depth the sample was encoded with.
    :rtype: ScanFacts
    """
    width_px, height_px = page.size_px
    return ScanFacts(
        width_px=width_px,
        height_px=height_px,
        color_mode=page.mode,
        dpi_x=page.dpi,
        dpi_y=page.dpi,
        bits_per_component=1 if page.mode is ColorMode.BILEVEL else 8,
        image_format='DjVu',
        width_mm=to_mm(width_px, units_per_inch=page.dpi),
        height_mm=to_mm(height_px, units_per_inch=page.dpi),
    )


def _write(kind: DjvuDocumentKind, directory: Path, *, pages: Sequence[DjvuPage] = PAGES) -> list[Path]:
    """Write the pages as one document of a kind, or as one file per page for the single-page kind.

    :param kind: Kind of the document.
    :type kind: DjvuDocumentKind
    :param directory: Existing directory to write into.
    :type directory: Path
    :param pages: Pages in order.
    :type pages: Sequence[DjvuPage]
    :returns: The files of the source, or the files of every source for the single-page kind.
    :rtype: list[Path]
    """
    match kind:
        case DjvuDocumentKind.BUNDLED:
            return [write_djvu_bundle(directory / 'book.djvu', pages=pages)]
        case DjvuDocumentKind.INDIRECT:
            return write_djvu_indirect(directory, pages=pages, index_name=INDEX_NAME)
        case DjvuDocumentKind.SINGLE_PAGE:
            return write_djvu_pages(directory, pages=pages)


def _broken_page(directory: Path, *, body: bytes) -> Path:
    """Write a file that starts as a single-page DjVu file and holds ``body`` after its header.

    :param directory: Existing directory to write into.
    :type directory: Path
    :param body: Bytes after the header, which no DjVu tool can read.
    :type body: bytes
    :returns: The written file.
    :rtype: Path
    """
    path = directory / 'page.djvu'
    path.write_bytes(HEADER_LAYOUT.pack(b'AT&T', b'FORM', len(body), b'DJVU', b'INFO', 0, 0, 0) + body)
    return path


class TestInspect:
    """Tests for SourceInspector.inspect() of DjVu sources, served by DjvuFormat."""

    @requires_djvulibre
    @pytest.mark.parametrize('kind', KINDS, ids=[kind.value for kind in KINDS])
    async def test_reports_the_facts_of_every_page_of_every_kind(
        self, fx_inspector: SourceInspector, tmp_path: Path, kind: DjvuDocumentKind
    ) -> None:
        """Verify the size, resolution, physical size, colour mode and bit depth of each page, whatever the kind.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param kind: Kind of the document the pages are written as.
        :type kind: DjvuDocumentKind
        """
        files = _write(kind, tmp_path)
        sources = [files] if kind is not DjvuDocumentKind.SINGLE_PAGE else [[path] for path in files]

        scans = [
            evolve(scan, extra={})
            for source in sources
            for scan in (await fx_inspector.inspect(SourceKind.DJVU, source)).scans
        ]

        assert scans == [_expected(page) for page in PAGES]

    @requires_djvulibre
    async def test_lists_the_chunks_of_each_page_in_file_order(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify a bilevel page holds only its INFO and JB2 mask chunks, and a colour page an IW44 background.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)

        color, _, bilevel = (await fx_inspector.inspect(SourceKind.DJVU, [book])).scans

        assert bilevel.extra[FactKey.DJVU_CHUNKS] == BILEVEL_CHUNKS
        assert color.extra[FactKey.DJVU_CHUNKS][0] == 'INFO'
        assert COLOR_CHUNK in color.extra[FactKey.DJVU_CHUNKS]

    @requires_djvulibre
    @pytest.mark.parametrize('kind', KINDS, ids=[kind.value for kind in KINDS])
    async def test_records_the_kind_and_counts_of_the_document(
        self, fx_inspector: SourceInspector, tmp_path: Path, kind: DjvuDocumentKind
    ) -> None:
        """Verify the kind, the page count and, for a document, the number of components its directory lists.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param kind: Kind of the document the pages are written as.
        :type kind: DjvuDocumentKind
        """
        files = _write(kind, tmp_path)
        source = files[:1] if kind is DjvuDocumentKind.SINGLE_PAGE else files

        analysis = await fx_inspector.inspect(SourceKind.DJVU, source)

        expected = {
            DjvuDocumentKind.BUNDLED: (len(PAGES), len(PAGES)),
            DjvuDocumentKind.INDIRECT: (len(PAGES), len(PAGES)),
            DjvuDocumentKind.SINGLE_PAGE: (1, None),
        }[kind]
        metadata = analysis.file_metadata
        assert metadata[FactKey.DJVU_KIND] == kind
        assert (metadata[FactKey.PAGE_COUNT], metadata.get(FactKey.COMPONENT_COUNT)) == expected

    @requires_djvulibre
    async def test_puts_cyrillic_metadata_into_the_suggestion_without_escapes(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify the title and authors come out as Cyrillic text, and every pair is kept in the document info.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)
        pairs = (
            f'title "{CYRILLIC_TITLE}"\nauthor "{CYRILLIC_AUTHOR}"\npublisher "Синодальная типография"\nyear "1902"\n'
        )
        edit_djvu(book, command='set-meta', script=pairs)

        analysis = await fx_inspector.inspect(SourceKind.DJVU, [book])

        suggestion = analysis.suggestion
        assert (suggestion.title, suggestion.contributors) == (
            CYRILLIC_TITLE,
            (Contributor(name=CYRILLIC_AUTHOR, role=ContributorRole.AUTHOR),),
        )
        assert (suggestion.publisher, suggestion.publication_year) == ('Синодальная типография', '1902')
        assert analysis.file_metadata[FactKey.DOCUMENT_INFO]['title'] == CYRILLIC_TITLE

    @requires_djvulibre
    async def test_suggests_editors_and_every_author_and_takes_no_date_but_the_year(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify authors split at semicolons, an editor keeps its role, and creation dates give no year.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)
        pairs = 'author "Ивановъ, И. И.; Петровъ, П. П."\neditor "Фёдоровъ, Ф. Ф."\nCreationDate "2024-05-12"\n'
        edit_djvu(book, command='set-meta', script=pairs)

        suggestion = (await fx_inspector.inspect(SourceKind.DJVU, [book])).suggestion

        assert (suggestion.contributors, suggestion.publication_year) == (
            (
                Contributor(name='Ивановъ, И. И.', role=ContributorRole.AUTHOR),
                Contributor(name='Петровъ, П. П.', role=ContributorRole.AUTHOR),
                Contributor(name='Фёдоровъ, Ф. Ф.', role=ContributorRole.EDITOR),
            ),
            '',
        )

    @requires_djvulibre
    @pytest.mark.parametrize(
        ('pairs', 'title'),
        [
            ('Title "From DocInfo"\n', 'From DocInfo'),
            ('Title "From DocInfo"\ntitle "From BibTeX"\n', 'From BibTeX'),
            ('Creator "Scanner"\n', ''),
        ],
        ids=['docinfo-key', 'bibtex-key-preferred', 'no-title'],
    )
    async def test_reads_the_title_from_either_spelling_of_the_key(
        self, fx_inspector: SourceInspector, tmp_path: Path, pairs: str, title: str
    ) -> None:
        """Verify the title comes from the DocInfo key when only that is set, and from the BibTeX key when both are.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param pairs: Metadata pairs to write into the document.
        :type pairs: str
        :param title: Title the suggestion must hold, or empty for none.
        :type title: str
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)
        edit_djvu(book, command='set-meta', script=pairs)

        analysis = await fx_inspector.inspect(SourceKind.DJVU, [book])

        assert analysis.suggestion.title == title

    @requires_djvulibre
    @pytest.mark.parametrize(
        ('outline', 'entries'),
        [
            ('', 0),
            ('(bookmarks ("Предисловие" "#1") ("Глава \\"первая\\" (начало)" "#2" ("Раздел" "#3")))', 3),
        ],
        ids=['no-outline', 'nested-outline'],
    )
    async def test_counts_the_entries_of_the_outline(
        self, fx_inspector: SourceInspector, tmp_path: Path, outline: str, entries: int
    ) -> None:
        """Verify the outline is counted at every depth, whatever brackets and quotes its titles hold.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param outline: The outline as ``djvused`` reads it, or empty for none.
        :type outline: str
        :param entries: Number of entries the outline holds.
        :type entries: int
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)
        if outline:
            edit_djvu(book, command='set-outline', script=outline)

        metadata = (await fx_inspector.inspect(SourceKind.DJVU, [book])).file_metadata

        assert (metadata[FactKey.HAS_OUTLINE], metadata[FactKey.OUTLINE_ENTRIES]) == (entries > 0, entries)

    @requires_djvulibre
    async def test_reports_the_text_layer_of_the_page_that_has_one(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify only the page carrying hidden text is reported with a text layer.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)
        edit_djvu(book, command='select 2; set-txt', script='(page 0 0 250 350 (line 0 0 250 20 "Слово"))')

        scans = (await fx_inspector.inspect(SourceKind.DJVU, [book])).scans

        assert [scan.has_text_layer for scan in scans] == [False, True, False]

    @requires_djvulibre
    async def test_refuses_an_indirect_document_and_names_the_missing_files(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Refuse an indirect document whose page files are missing from the source, naming every missing file.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        index, first, *_ = _write(DjvuDocumentKind.INDIRECT, tmp_path)

        with pytest.raises(UnsupportedSourceError, match=MISSING_MATCH):
            await fx_inspector.inspect(SourceKind.DJVU, [index, first])

    @requires_djvulibre
    async def test_reads_an_indirect_document_whatever_the_order_of_its_files(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify the index is found by its header, and the pages follow the index and not the order of the files.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        files = _write(DjvuDocumentKind.INDIRECT, tmp_path)

        scans = (await fx_inspector.inspect(SourceKind.DJVU, files[::-1])).scans

        assert [scan.width_px for scan in scans] == [page.size_px[0] for page in PAGES]

    @requires_djvulibre
    async def test_refuses_a_file_that_is_not_djvu(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Refuse a file that does not start as a DjVu file, before any tool runs.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / 'page.djvu'
        path.write_bytes(b'%PDF-1.7 not a DjVu file at all, though it is long enough to hold a header')

        with pytest.raises(UnsupportedSourceError, match=NOT_DJVU_MATCH):
            await fx_inspector.inspect(SourceKind.DJVU, [path])

    @requires_djvulibre
    async def test_refuses_a_damaged_file_without_showing_the_tool_output(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Refuse a file with a DjVu header and a body no tool reads, with a message naming only the file.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = _broken_page(tmp_path, body=b'\x00' * 64)

        with pytest.raises(UnsupportedSourceError, match=DAMAGED_MATCH) as error:
            await fx_inspector.inspect(SourceKind.DJVU, [path])

        assert 'ByteStream' not in str(error.value)

    @requires_djvulibre
    async def test_refuses_a_page_that_includes_shared_data_missing_from_the_upload(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Refuse a single-page file that names a shared data file no one uploaded.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = write_djvu_including(tmp_path / 'includes.djvu', shared_name='shared.djvi')

        with pytest.raises(UnsupportedSourceError, match=r'^includes\.djvu needs shared\.djvi'):
            await fx_inspector.inspect(SourceKind.DJVU, [page])

    async def test_refuses_a_call_that_runs_past_the_timeout(self, tmp_path: Path) -> None:
        """Refuse a file whose tool call runs past the timeout, and give the tool the configured timeout.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        tools = DjvuLibreTools(djvused=Path('djvused'), djvudump=Path('djvudump'), ddjvu=Path('ddjvu'))
        djvu = DjvuFormat(tools=tools, jpeg_quality=JPEG_QUALITY, timeout_s=TIMEOUT_S)
        path = _broken_page(tmp_path, body=b'')

        with (
            patch(RUN_TARGET, side_effect=subprocess.TimeoutExpired(cmd='djvudump', timeout=TIMEOUT_S)) as run,
            pytest.raises(UnsupportedSourceError, match=TIMEOUT_MATCH),
        ):
            djvu.inspect([path])

        assert run.call_args.kwargs['timeout'] == TIMEOUT_S

    @patch(RUN_TARGET)
    async def test_refuses_a_tool_that_cannot_run(self, run: MagicMock, tmp_path: Path) -> None:
        """Refuse a file when the operating system cannot start the tool, which is a server that lost DjVuLibre.

        :param run: Patched ``subprocess.run``, which fails as a missing executable does.
        :type run: MagicMock
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        run.side_effect = FileNotFoundError
        tools = DjvuLibreTools(djvused=Path('djvused'), djvudump=Path('djvudump'), ddjvu=Path('ddjvu'))
        djvu = DjvuFormat(tools=tools, jpeg_quality=JPEG_QUALITY, timeout_s=TIMEOUT_S)

        with pytest.raises(UnsupportedSourceError, match=r'^page\.djvu cannot be read: the DjVuLibre tool djvudump'):
            djvu.inspect([_broken_page(tmp_path, body=b'')])

    @requires_djvulibre
    async def test_rejects_files_that_are_not_one_document(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Reject two page files, which are no document, as a caller that did not group the upload would pass them.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        first, second, _ = _write(DjvuDocumentKind.SINGLE_PAGE, tmp_path)

        with pytest.raises(ValueError, match=r'^A DjVu source is one document file'):
            await fx_inspector.inspect(SourceKind.DJVU, [first, second])


def _named(root: Path, files: Sequence[Path]) -> dict[str, Path]:
    """Name files by their path under ``root``, in the order given, as the staged files of an upload are.

    :param root: Directory the upload was staged in.
    :type root: Path
    :param files: Paths of the files, in the order of the upload.
    :type files: Sequence[Path]
    :returns: Each path by its ``/`` separated relative path.
    :rtype: dict[str, Path]
    """
    return {path.relative_to(root).as_posix(): path for path in files}


def _source(*names: str) -> UploadedSource:
    """Return an expected DjVu source made of the named files.

    :param names: Names of the files, the main file first.
    :type names: str
    :returns: The source as the reader must group it.
    :rtype: UploadedSource
    """
    return UploadedSource(kind=SourceKind.DJVU, file_type=FileType.DJVU, names=list(names))


class TestGroup:
    """Tests for SourceInspector.group() of DjVu files, served by DjvuFormat."""

    @requires_djvulibre
    async def test_makes_one_source_of_an_index_and_all_its_files(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify an indirect document is one source, its index first and then its page files in the order of the index.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        files = _write(DjvuDocumentKind.INDIRECT, tmp_path)

        sources = await fx_inspector.group(_named(tmp_path, files[::-1]))

        assert list(sources) == [_source(INDEX_NAME, 'p0001.djvu', 'p0002.djvu', 'p0003.djvu')]

    @requires_djvulibre
    async def test_joins_an_index_only_with_the_files_of_its_own_folder(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify two volumes whose page files share their names are two sources, each with its own files.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        for volume in ('vol1', 'vol2'):
            (tmp_path / volume).mkdir()
        first = write_djvu_indirect(tmp_path / 'vol1', pages=PAGES[:2])
        second = write_djvu_indirect(tmp_path / 'vol2', pages=PAGES[:2])

        sources = await fx_inspector.group(_named(tmp_path, [*second, *first]))

        assert list(sources) == [
            _source('vol2/index.djvu', 'vol2/p0001.djvu', 'vol2/p0002.djvu'),
            _source('vol1/index.djvu', 'vol1/p0001.djvu', 'vol1/p0002.djvu'),
        ]

    @requires_djvulibre
    async def test_makes_a_source_of_every_document_and_page_file_in_the_order_of_the_upload(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify two indirect documents, a bundled one, single pages and a file that is no DjVu group as one upload.

        Every index takes only the files it names, a bundled document and a single page are sources of their own, and
        so is a file no tool reads, which ``inspect`` refuses by name later. The sources follow their main files in the
        upload, whatever their names, since the order of the upload is the order of the book.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        pages = [PAGES[0], PAGES[1]]
        first = write_djvu_indirect(tmp_path, pages=pages, index_name='a-index.djvu', prefix='a')
        second = write_djvu_indirect(tmp_path, pages=pages, index_name='b-index.djvu', prefix='b')
        bundle = write_djvu_bundle(tmp_path / 'c-book.djvu', pages=pages)
        singles = write_djvu_pages(tmp_path, pages=pages, prefix='d')
        notes = tmp_path / 'e-notes.djvu'
        notes.write_bytes(b'not a DjVu file, though long enough to hold a header')
        upload = [notes, *singles[::-1], bundle, *second[::-1], *first]

        sources = await fx_inspector.group(_named(tmp_path, upload))

        assert list(sources) == [
            _source('e-notes.djvu'),
            _source('d0002.djvu'),
            _source('d0001.djvu'),
            _source('c-book.djvu'),
            _source('b-index.djvu', 'b0001.djvu', 'b0002.djvu'),
            _source('a-index.djvu', 'a0001.djvu', 'a0002.djvu'),
        ]

    @requires_djvulibre
    async def test_keeps_an_index_with_the_files_that_are_there_when_some_are_missing(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify an incomplete document is still one source, so that ``inspect`` refuses it whole and lists the rest.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        index, first, *_ = _write(DjvuDocumentKind.INDIRECT, tmp_path)

        sources = await fx_inspector.group(_named(tmp_path, [index, first]))

        assert list(sources) == [_source(INDEX_NAME, 'p0001.djvu')]

    def test_leaves_every_file_a_source_of_its_own_without_the_tools(self) -> None:
        """Verify grouping needs no tools when there are none, since it then joins nothing."""
        djvu = DjvuFormat(tools=None, jpeg_quality=JPEG_QUALITY, timeout_s=TIMEOUT_S)
        files = [Path('index.djvu'), Path('p0001.djvu')]

        assert djvu.group(files) == [[files[0]], [files[1]]]


@requires_djvulibre
class TestExtract:
    """Tests for PageRasterizer.extract() of DjVu scans, served by DjvuFormat."""

    @pytest.mark.parametrize('kind', KINDS, ids=[kind.value for kind in KINDS])
    @pytest.mark.parametrize('number', range(len(PAGES)))
    async def test_writes_the_page_as_jpeg_at_its_native_resolution(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path, kind: DjvuDocumentKind, number: int
    ) -> None:
        """Verify the JPEG has the pixel size of the page's INFO chunk and the mode of its colour, for every kind.

        A bilevel page is written gray, since a JPEG holds no single bit.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param kind: Kind of the document the pages are written as.
        :type kind: DjvuDocumentKind
        :param number: Number of the scan to write, starting at 0.
        :type number: int
        """
        files = _write(kind, tmp_path)
        single = kind is DjvuDocumentKind.SINGLE_PAGE
        target = tmp_path / TARGET_NAME
        page = PAGES[number]

        await fx_rasterizer.extract(
            SourceKind.DJVU,
            [files[number]] if single else files,
            0 if single else number,
            target,
            full=Rendition.FULL_JPEG,
        )

        with Image.open(target) as image:
            assert (image.format, image.size, image.mode) == (JPEG_FORMAT, page.size_px, JPEG_MODES[page.mode])

    @pytest.mark.parametrize('kind', KINDS, ids=[kind.value for kind in KINDS])
    @pytest.mark.parametrize('number', range(len(PAGES)))
    async def test_writes_the_page_as_png_keeping_a_bilevel_page_at_one_bit(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path, kind: DjvuDocumentKind, number: int
    ) -> None:
        """Verify the PNG has the pixel size of the page and the mode of its colour, a bilevel page being 1-bit.

        A 1-bit image holds exactly two values, so nothing smooths the strokes of a bilevel page.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param kind: Kind of the document the pages are written as.
        :type kind: DjvuDocumentKind
        :param number: Number of the scan to write, starting at 0.
        :type number: int
        """
        files = _write(kind, tmp_path)
        single = kind is DjvuDocumentKind.SINGLE_PAGE
        target = tmp_path / PNG_TARGET_NAME
        page = PAGES[number]

        await fx_rasterizer.extract(
            SourceKind.DJVU,
            [files[number]] if single else files,
            0 if single else number,
            target,
            full=Rendition.FULL_PNG,
        )

        with Image.open(target) as image:
            assert (image.format, image.size, image.mode) == (PNG_FORMAT, page.size_px, PNG_MODES[page.mode])

    async def test_refuses_a_scan_the_document_does_not_have(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path
    ) -> None:
        """Raise IndexError for a scan past the last page and for a negative one, and write nothing.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        [book] = _write(DjvuDocumentKind.BUNDLED, tmp_path)
        target = tmp_path / TARGET_NAME

        for number in (len(PAGES), -1):
            with pytest.raises(IndexError, match=r'^book\.djvu has no page'):
                await fx_rasterizer.extract(SourceKind.DJVU, [book], number, target, full=Rendition.FULL_JPEG)
        assert not target.exists()

    async def test_refuses_a_damaged_page(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Refuse a file whose page cannot be rendered.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = _broken_page(tmp_path, body=b'\x00' * 64)

        with pytest.raises(UnsupportedSourceError, match=DAMAGED_MATCH):
            await fx_rasterizer.extract(SourceKind.DJVU, [path], 0, tmp_path / TARGET_NAME, full=Rendition.FULL_JPEG)


class TestImagingProviderWithoutDjvulibre:
    """Tests for the imaging provider on a server without the DjVuLibre tools."""

    @patch(LOCATE_TARGET, return_value=None)
    async def test_warns_at_start_and_refuses_djvu_naming_the_package(
        self, locate: MagicMock, fx_imaging: AsyncContainer, caplog: pytest.LogCaptureFixture, tmp_path: Path
    ) -> None:
        """Verify the reader is still built, the log carries one warning naming the package, and DjVu is refused.

        :param locate: Patched search for the tools, which finds none.
        :type locate: MagicMock
        :param fx_imaging: Container holding only the imaging provider.
        :type fx_imaging: AsyncContainer
        :param caplog: Fixture capturing log records.
        :type caplog: pytest.LogCaptureFixture
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with caplog.at_level(logging.WARNING):
            inspector = await fx_imaging.get(SourceInspector)

        locate.assert_called_once_with()
        assert [record.getMessage() for record in caplog.records if 'djvulibre' in record.getMessage()]
        with pytest.raises(UnsupportedSourceError, match=r'djvulibre package'):
            await inspector.inspect(SourceKind.DJVU, [tmp_path / 'book.djvu'])
