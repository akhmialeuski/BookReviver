"""DjVu sources read with the DjVuLibre command-line tools: the facts of every page, and each page written as JPEG.

A DjVu file is one of three kinds, told apart by its first bytes before any tool runs: a bundled document holding every
page, an indirect document, which is a small index file naming one file per page beside it, and a single-page file.
A bundled document and a single-page file are a source of their own, and an indirect document is one source made of
the index and all its files, the only source of several files in the book. The kind is the ``DjvuDocumentKind`` that is
recorded with the source. ``group`` finds the index files of an upload and joins each with the files its
``djvused -e ls`` names, since no service reads files and only the index knows which files are its own. An index whose
files are not all in the upload is still one source, and ``inspect`` refuses it with the list of missing files, so the
files of the upload that are complete are imported. The files of one source may reach ``inspect`` and ``extract`` in
any order, so the index is found by its header.

DjVuLibre is used through its tools and not through the Python bindings, because the bindings are built from source
against the DjVuLibre headers, which would make ``uv sync`` fail on a machine without them, and because a tool that
crashes on a damaged file takes down its own process and not the worker. Each call is one synchronous ``subprocess.run``
with a list of arguments, no shell, a timeout and the exit status checked, and the formats already run in a worker
thread, so the event loop is never blocked. A tool that fails, hangs or is not installed becomes an
``UnsupportedSourceError`` that names the file, while the tool's own message goes to the log only.

The size, resolution and chunks of every page come from the ``INFO`` line and the chunk lines of one ``djvudump`` call
per file, since ``djvused`` would give the size of a page but not its resolution and would need a process per page. The
colour mode follows the chunks: a page holding only the ``Sjbz`` mask is bilevel, and a page with an IW44 layer is
colour when the layer says so and gray otherwise. Pages are rendered with ``ddjvu`` at native resolution into a
temporary PNM file, and Pillow writes the JPEG at the configured quality or the PNG. A bilevel page is rendered as a
one-bit image and written as a 1-bit PNG, and as a gray JPEG in the format a JPEG has for it.
"""

import enum
import logging
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self, override

from attrs import frozen
from PIL import Image, ImageChops

from bookreviver.adapters.imaging.common import GRAY_MODE, FactKey, to_mm, write_full
from bookreviver.adapters.imaging.reader import SourceFormat
from bookreviver.adapters.imaging.suggestions import SuggestionBuilder
from bookreviver.domain.enums import ColorMode, DjvuDocumentKind, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import ScanFacts, SourceAnalysis

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.enums import Rendition

logger = logging.getLogger(__name__)

# The layout of the start of every DjVu file: the IFF magic, the FORM chunk with its length and type, and the first
# chunk with its identifier, length, flags and the number of component files
DJVU_MAGIC: bytes = b'AT&T'
IFF_FORM: bytes = b'FORM'
PAGE_FORM: str = 'DJVU'
DOCUMENT_FORM: bytes = b'DJVM'
DIRECTORY_CHUNK: bytes = b'DIRM'
HEADER_LAYOUT: struct.Struct = struct.Struct('>4s4sI4s4sIBH')
# The top bit of the flags byte of the directory chunk is set in a bundled document
BUNDLED_FLAG: int = 0x80

DJVU_IMAGE_FORMAT: str = 'DjVu'
PNM_FORMAT: str = 'pnm'
NATIVE_SCALE_OPTION: str = '-1'
# Pillow mode of a rendered page that DjVu holds in gray, which ddjvu makes an RGB image of
RGB_MODE: str = 'RGB'
BILEVEL_BITS: int = 1
DEFAULT_BITS: int = 8
# ``djvudump`` marks the first chunk of a colour IW44 layer
COLOR_MARK: str = '(color)'
LIBRARY_PACKAGE: str = 'djvulibre'

INFO_PATTERN: re.Pattern[str] = re.compile(r'DjVu (\d+)x(\d+), v\d+, (\d+) dpi')
FORM_PATTERN: re.Pattern[str] = re.compile(r'^\s*FORM:(\w+) \[')
CHUNK_PATTERN: re.Pattern[str] = re.compile(r'^\s+(\S{4}) \[\d+\]\s*(.*)$')
INCLUDED_PATTERN: re.Pattern[str] = re.compile(r'\{(.*)\}')
COMPONENT_PATTERN: re.Pattern[str] = re.compile(r'^\s*(?:\d+\s+)?([PIAT])\s+\d+\s+(\S+)')
META_PATTERN: re.Pattern[str] = re.compile(r'^(\S+)\s+"(.*)"$')
ESCAPE_PATTERN: re.Pattern[str] = re.compile(r'\\(?:([0-7]{3})|(.))')
STRING_PATTERN: re.Pattern[str] = re.compile(r'"(?:[^"\\]|\\.)*"')
BOOKMARK_PATTERN: re.Pattern[str] = re.compile(r'\(\s*""')
NEWLINE_ESCAPE: str = 'n'
EMPTY_STRING: str = '""'


class DjvuChunk(enum.StrEnum):
    """Identifiers of the chunks of a DjVu page that decide its colour mode, its text layer and its includes."""

    INFO = 'INFO'
    MASK = 'Sjbz'
    BACKGROUND = 'BG44'
    FOREGROUND = 'FG44'
    TEXT = 'TXTz'
    PLAIN_TEXT = 'TXTa'
    INCLUDE = 'INCL'


class DjvuComponentKind(enum.StrEnum):
    """Letters ``djvused ls`` prints for the kind of a component of a document."""

    PAGE = 'P'
    SHARED = 'I'
    ANNOTATIONS = 'A'
    THUMBNAILS = 'T'


@frozen(kw_only=True)
class DjvuLibreTools:
    """Paths of the DjVuLibre tools the format runs.

    :ivar djvused: The ``djvused`` tool, which lists components and prints metadata and the outline.
    :ivar djvudump: The ``djvudump`` tool, which prints the chunks of every page.
    :ivar ddjvu: The ``ddjvu`` tool, which renders a page.
    """

    djvused: Path
    djvudump: Path
    ddjvu: Path

    @classmethod
    def locate(cls) -> Self | None:
        """Find the tools on the search path of the server.

        :returns: The tools, or None when any of them is not installed.
        :rtype: Self | None
        """
        djvused, djvudump, ddjvu = (shutil.which(name) for name in ('djvused', 'djvudump', 'ddjvu'))
        if not (djvused and djvudump and ddjvu):
            return None
        return cls(djvused=Path(djvused), djvudump=Path(djvudump), ddjvu=Path(ddjvu))


@frozen(kw_only=True)
class DjvuHeader:
    """What the first bytes of a DjVu file say about it.

    :ivar kind: Whether the file is a bundled document, an indirect index or a single page.
    :ivar component_count: Number of component files the document directory lists, or None for a single page.
    """

    kind: DjvuDocumentKind
    component_count: int | None = None

    @classmethod
    def read(cls, path: Path) -> Self:
        """Read the header of a DjVu file.

        :param path: Local path of the file.
        :type path: Path
        :returns: The header of the file.
        :rtype: Self
        :raises UnsupportedSourceError: If the file does not start as a DjVu document or page.
        :raises OSError: If the file cannot be read.
        """
        with path.open('rb') as file:
            head = file.read(HEADER_LAYOUT.size)
        err_msg = f'{path.name} is not a DjVu file. Upload a DjVu file, or upload page images as image files.'
        if len(head) < HEADER_LAYOUT.size:
            raise UnsupportedSourceError(err_msg)
        magic, form, _, form_type, chunk_id, _, flags, count = HEADER_LAYOUT.unpack(head)
        if magic != DJVU_MAGIC or form != IFF_FORM:
            raise UnsupportedSourceError(err_msg)
        if form_type == PAGE_FORM.encode():
            return cls(kind=DjvuDocumentKind.SINGLE_PAGE)
        if form_type != DOCUMENT_FORM or chunk_id != DIRECTORY_CHUNK:
            err_msg = f'{path.name} is a DjVu file, but neither a document nor a page. Upload a whole DjVu document.'
            raise UnsupportedSourceError(err_msg)
        kind = DjvuDocumentKind.BUNDLED if flags & BUNDLED_FLAG else DjvuDocumentKind.INDIRECT
        return cls(kind=kind, component_count=count)


@frozen(kw_only=True)
class DjvuComponent:
    """One component of an indirect document, as ``djvused ls`` lists it.

    :ivar kind: Whether the component holds the data of a page, shared data, annotations or thumbnails.
    :ivar name: Name of the file that holds the component, beside the index.
    """

    kind: DjvuComponentKind
    name: str


class DjvuFormat(SourceFormat):
    """Describes DjVu pages from ``djvudump`` and ``djvused``, and renders them with ``ddjvu``."""

    kind = SourceKind.DJVU

    def __init__(self, *, tools: DjvuLibreTools | None, jpeg_quality: int, timeout_s: int) -> None:
        """Run the given tools, encoding rendered pages at ``jpeg_quality``.

        :param tools: The DjVuLibre tools, or None when they are not installed, which refuses every DjVu source.
        :type tools: DjvuLibreTools | None
        :param jpeg_quality: JPEG quality from 1 to 100 of every rendered page.
        :type jpeg_quality: int
        :param timeout_s: Seconds one tool call may run before the file is refused.
        :type timeout_s: int
        """
        self._tools = tools
        self._jpeg_quality = jpeg_quality
        self._timeout_s = timeout_s

    @override
    def group(self, files: Sequence[Path]) -> list[Sequence[Path]]:
        """Join the index file of each indirect document with the files its index names, and leave the rest alone.

        A bundled document and a single-page file are a source of their own. A file that is not DjVu, and an index that
        the tools cannot read, are left as sources of their own too, so ``inspect`` refuses them by name without
        stopping the other files. A file the index names but the upload lacks is not joined, and ``inspect`` reports it
        as missing. Without the tools every file is a source of its own, since only the tools read an index.

        :param files: Local paths of the staged DjVu files, in the natural order of their names.
        :type files: Sequence[Path]
        :returns: The files of each source in the order of their main files, the index first and then its files in the
                  order of the index, all its components and not only the pages.
        :rtype: list[Sequence[Path]]
        """
        if self._tools is None:
            return super().group(files)
        by_name = {path.name: path for path in files}
        members: dict[Path, list[Path]] = {}
        claimed: set[Path] = set()
        for path in files:
            try:
                if DjvuHeader.read(path).kind is not DjvuDocumentKind.INDIRECT:
                    continue
                components = self._components(path)
            except UnsupportedSourceError, OSError:
                continue
            members[path] = [
                by_name[component.name]
                for component in components
                if component.name in by_name and by_name[component.name] not in {path, *claimed}
            ]
            claimed.update(members[path])
        return [[path, *members.get(path, [])] for path in files if path not in claimed]

    @override
    def inspect(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe every page of one DjVu document or page file, with its metadata and outline.

        :param files: Local paths of the files of the source in any order: one bundled document or single-page file,
                      or the index of an indirect document with every file it names.
        :type files: Sequence[Path]
        :returns: Facts of every page in page order, the kind of the document, its page count, metadata and outline,
                  and the description found in the metadata.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the tools are not installed, a file is not a readable DjVu file, a file of an
                                        indirect document is missing, or a page includes shared data that is missing.
        :raises ValueError: If the files are not one document, or one index with its components.
        """
        tools = self._installed()
        main, header = self._main(files)
        if header.kind is DjvuDocumentKind.INDIRECT:
            by_name = {path.name: path for path in files}
            components = self._components(main)
            if missing := [component.name for component in components if component.name not in by_name]:
                err_msg = (
                    f'{main.name} is an indirect DjVu document, and {len(missing)} of its files are missing from the '
                    f'upload: {", ".join(missing)}. Upload the index and all its page files together.'
                )
                raise UnsupportedSourceError(err_msg)
            pages = [
                page
                for component in components
                if component.kind is DjvuComponentKind.PAGE
                for page in self._pages(by_name[component.name], kind=header.kind)
            ]
        else:
            pages = self._pages(main, kind=header.kind)
        if (page_count := self._page_count(main)) != len(pages):
            err_msg = f'{main.name} cannot be read as DjVu: it declares {page_count} pages and holds {len(pages)}.'
            raise UnsupportedSourceError(err_msg)

        info = self._metadata(main)
        outline = self._run(tools.djvused, '-u', '-e', 'print-outline', main, subject=main)
        outline_entries = len(BOOKMARK_PATTERN.findall(STRING_PATTERN.sub(EMPTY_STRING, outline)))
        file_metadata: dict[str, Any] = {
            FactKey.DJVU_KIND: header.kind,
            FactKey.PAGE_COUNT: page_count,
            FactKey.DOCUMENT_INFO: info,
            FactKey.HAS_OUTLINE: outline_entries > 0,
            FactKey.OUTLINE_ENTRIES: outline_entries,
        }
        if header.component_count is not None:
            file_metadata[FactKey.COMPONENT_COUNT] = header.component_count
        suggestion = SuggestionBuilder.from_djvu_meta(info)
        return SourceAnalysis(kind=SourceKind.DJVU, scans=pages, file_metadata=file_metadata, suggestion=suggestion)

    @override
    def extract(self, files: Sequence[Path], *, number: int, target: Path, full: Rendition) -> None:
        """Render the page at its native resolution and write it as a JPEG or a PNG, gray pages in gray.

        A bilevel page keeps its one bit in a PNG, and is written gray as a JPEG.

        :param files: Local paths of the files of the source in any order, as for ``inspect``.
        :type files: Sequence[Path]
        :param number: Number of the page in the document, starting at 0.
        :type number: int
        :param target: Path to write the image at.
        :type target: Path
        :param full: Format to write, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
        :type full: Rendition
        :raises UnsupportedSourceError: If the tools are not installed, or the page cannot be rendered.
        :raises IndexError: If the document has fewer pages than ``number + 1``.
        :raises ValueError: If the files are not one document, or one index with its components, or ``full`` is not a
                            format of the full image.
        """
        tools = self._installed()
        main, _ = self._main(files)
        page_count = self._page_count(main)
        if not 0 <= number < page_count:
            err_msg = f'{main.name} has no page {number}: it holds {page_count} pages.'
            raise IndexError(err_msg)
        with tempfile.TemporaryDirectory() as directory:
            rendered = Path(directory) / f'page.{PNM_FORMAT}'
            # ddjvu numbers pages from 1, and renders at the resolution of the page when it gets no scale option
            options = (f'-format={PNM_FORMAT}', f'-page={number + 1}', NATIVE_SCALE_OPTION)
            self._run(tools.ddjvu, *options, main, rendered, subject=main)
            with Image.open(rendered) as image:
                image.load()
                # ddjvu makes an RGB image of a page that DjVu holds in gray
                page = image.convert(GRAY_MODE) if image.mode == RGB_MODE and _is_gray(image) else image
                write_full(page, target, full=full, jpeg_quality=self._jpeg_quality)

    def _installed(self) -> DjvuLibreTools:
        """Return the tools, or refuse the source when they are not installed.

        :returns: The DjVuLibre tools.
        :rtype: DjvuLibreTools
        :raises UnsupportedSourceError: If the tools are not installed on the server.
        """
        if self._tools is None:
            err_msg = (
                'Reading DjVu files is not set up on this server. Ask its administrator to install the '
                f'{LIBRARY_PACKAGE} package, or convert the book to PDF or page images.'
            )
            raise UnsupportedSourceError(err_msg)
        return self._tools

    def _main(self, files: Sequence[Path]) -> tuple[Path, DjvuHeader]:
        """Pick the file that describes the source, the only file or the index of an indirect document.

        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :returns: The main file and its header.
        :rtype: tuple[Path, DjvuHeader]
        :raises UnsupportedSourceError: If a file is not a readable DjVu file.
        :raises ValueError: If the files are not one document, or one index with its components.
        """
        headers = {path: DjvuHeader.read(path) for path in files}
        indexes = [path for path, header in headers.items() if header.kind is DjvuDocumentKind.INDIRECT]
        if len(files) == 1:
            return files[0], headers[files[0]]
        if len(indexes) != 1:
            err_msg = f'A DjVu source is one document file, or one index with its files, not {len(files)} files.'
            raise ValueError(err_msg)
        return indexes[0], headers[indexes[0]]

    def _components(self, index: Path) -> list[DjvuComponent]:
        """List the components of an indirect document in the order of its index.

        :param index: Local path of the index file.
        :type index: Path
        :returns: The kind and file name of every component.
        :rtype: list[DjvuComponent]
        :raises UnsupportedSourceError: If the tools are not installed or fail on the index.
        """
        output = self._run(self._installed().djvused, '-e', 'ls', index, subject=index)
        return [
            DjvuComponent(kind=DjvuComponentKind(match[1]), name=match[2])
            for line in output.splitlines()
            if (match := COMPONENT_PATTERN.match(line))
        ]

    def _page_count(self, main: Path) -> int:
        """Return the number of pages of the document the file is, or the index names.

        :param main: Local path of the document or of the index.
        :type main: Path
        :returns: The number of pages.
        :rtype: int
        :raises UnsupportedSourceError: If the tools are not installed or fail on the file, or the file holds no page.
        """
        if (count := int(self._run(self._installed().djvused, '-e', 'n', main, subject=main))) < 1:
            err_msg = f'{main.name} cannot be read as DjVu: the file is damaged, and holds no page.'
            raise UnsupportedSourceError(err_msg)
        return count

    def _metadata(self, main: Path) -> dict[str, str]:
        """Read the document metadata as pairs of a key and a text in UTF-8.

        :param main: Local path of the document or of the index.
        :type main: Path
        :returns: The pairs ``print-meta`` prints, keys as the file spells them.
        :rtype: dict[str, str]
        :raises UnsupportedSourceError: If the tools are not installed or fail on the file.
        """
        output = self._run(self._installed().djvused, '-u', '-e', 'print-meta', main, subject=main)
        pairs = (match.groups() for line in output.splitlines() if (match := META_PATTERN.match(line)))
        return {key: ESCAPE_PATTERN.sub(_unescape, value) for key, value in pairs}

    def _pages(self, path: Path, *, kind: DjvuDocumentKind) -> list[ScanFacts]:
        """Describe the pages that ``djvudump`` finds in a file.

        :param path: Local path of a bundled document, a single-page file, or a page file of an indirect document.
        :type path: Path
        :param kind: Kind of the document the file belongs to, since only a lone page must hold all its own data.
        :type kind: DjvuDocumentKind
        :returns: Facts of every page in file order.
        :rtype: list[ScanFacts]
        :raises UnsupportedSourceError: If the tools are not installed or fail on the file, a page has no ``INFO``
                                        chunk, or a lone page includes shared data.
        """
        pages: list[list[tuple[str, str]]] = []
        current: list[tuple[str, str]] | None = None
        for line in self._run(self._installed().djvudump, path, subject=path).splitlines():
            if form := FORM_PATTERN.match(line):
                # Only a FORM:DJVU is a page, and the shared data and thumbnails of a bundle are not
                current = [] if form[1] == PAGE_FORM else None
                if current is not None:
                    pages.append(current)
            elif current is not None and (chunk := CHUNK_PATTERN.match(line)):
                current.append((chunk[1], chunk[2]))
        return [_scan_facts(page, path=path, kind=kind) for page in pages]

    def _run(self, tool: Path, *arguments: str | Path, subject: Path) -> str:
        """Run one tool call, and return what it printed.

        :param tool: Path of the tool.
        :type tool: Path
        :param arguments: Options and paths to pass to the tool.
        :type arguments: str | Path
        :param subject: The file being read, named in the error message.
        :type subject: Path
        :returns: The standard output of the tool.
        :rtype: str
        :raises UnsupportedSourceError: If the tool cannot run, fails or runs past the timeout.
        """
        try:
            done = subprocess.run([tool, *arguments], check=True, capture_output=True, timeout=self._timeout_s)
        except subprocess.TimeoutExpired as error:
            err_msg = f'{subject.name} took too long to read as DjVu: the file is damaged or too complex.'
            raise UnsupportedSourceError(err_msg) from error
        except subprocess.CalledProcessError as error:
            logger.warning(
                '%s failed on %s: %s', tool.name, subject.name, error.stderr.decode(errors='replace').strip()
            )
            err_msg = f'{subject.name} cannot be read as DjVu: the file is damaged. Upload an intact file.'
            raise UnsupportedSourceError(err_msg) from error
        except OSError as error:
            logger.warning('%s cannot run: %s', tool.name, error)
            err_msg = f'{subject.name} cannot be read: the DjVuLibre tool {tool.name} cannot run on this server.'
            raise UnsupportedSourceError(err_msg) from error
        return done.stdout.decode(errors='replace')


def _scan_facts(chunks: Sequence[tuple[str, str]], *, path: Path, kind: DjvuDocumentKind) -> ScanFacts:
    """Describe one page from the chunks ``djvudump`` printed for it.

    :param chunks: Identifier and description of every chunk of the page in file order.
    :type chunks: Sequence[tuple[str, str]]
    :param path: File the page comes from, named in errors.
    :type path: Path
    :param kind: Kind of the document, which forbids a lone page to include shared data.
    :type kind: DjvuDocumentKind
    :returns: Size, resolution, physical size, colour mode, text layer and chunk identifiers of the page.
    :rtype: ScanFacts
    :raises UnsupportedSourceError: If the page has no readable ``INFO`` chunk, or is a lone page that includes shared
                                    data missing from the upload.
    """
    described = dict(chunks)
    if not (info := INFO_PATTERN.search(described.get(DjvuChunk.INFO, ''))):
        err_msg = f'{path.name} cannot be read as DjVu: a page has no size. Upload an intact file.'
        raise UnsupportedSourceError(err_msg)
    if kind is DjvuDocumentKind.SINGLE_PAGE and (included := described.get(DjvuChunk.INCLUDE)) is not None:
        name = match[1] if (match := INCLUDED_PATTERN.search(included)) else 'a shared file'
        err_msg = f'{path.name} needs {name}, which is not in the upload. Upload it with the whole DjVu document.'
        raise UnsupportedSourceError(err_msg)
    width_px, height_px, dpi = (int(value) for value in info.groups())
    identifiers = {identifier for identifier, _ in chunks}
    layers = identifiers & {DjvuChunk.BACKGROUND, DjvuChunk.FOREGROUND}
    if not layers and DjvuChunk.MASK in identifiers:
        color_mode = ColorMode.BILEVEL
    elif any(COLOR_MARK in description for identifier, description in chunks if identifier in layers):
        color_mode = ColorMode.COLOR
    else:
        color_mode = ColorMode.GRAY
    return ScanFacts(
        width_px=width_px,
        height_px=height_px,
        color_mode=color_mode,
        dpi_x=dpi or None,
        dpi_y=dpi or None,
        bits_per_component=BILEVEL_BITS if color_mode is ColorMode.BILEVEL else DEFAULT_BITS,
        image_format=DJVU_IMAGE_FORMAT,
        width_mm=to_mm(width_px, units_per_inch=dpi) if dpi else None,
        height_mm=to_mm(height_px, units_per_inch=dpi) if dpi else None,
        has_text_layer=bool(identifiers & {DjvuChunk.TEXT, DjvuChunk.PLAIN_TEXT}),
        extra={FactKey.DJVU_CHUNKS: [identifier for identifier, _ in chunks]},
    )


def _unescape(match: re.Match[str]) -> str:
    """Resolve one backslash escape of a string ``djvused`` printed.

    :param match: An escape: three octal digits, or one escaped character.
    :type match: re.Match[str]
    :returns: The character the escape stands for.
    :rtype: str
    """
    if match[1]:
        return chr(int(match[1], 8))
    escaped = str(match[2])
    return '\n' if escaped == NEWLINE_ESCAPE else escaped


def _is_gray(image: Image.Image) -> bool:
    """Return whether the three channels of an RGB image are equal in every pixel.

    :param image: The RGB image to test.
    :type image: Image.Image
    :returns: True when the image holds no colour.
    :rtype: bool
    """
    red, green, blue = image.split()
    return not ImageChops.difference(red, green).getbbox() and not ImageChops.difference(green, blue).getbbox()
