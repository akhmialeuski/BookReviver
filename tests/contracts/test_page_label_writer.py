"""Contract of the PageLabelWriter port, run against the adapter of every output format and the one in memory."""

from typing import TYPE_CHECKING, Any, NamedTuple

import pymupdf
import pytest
from defusedxml.ElementTree import parse

from bookreviver.adapters.imaging import EpubPageList, MemoryPageLabelWriter, PdfLabelWriter
from bookreviver.adapters.imaging.pdf_labels import read_label_rules
from bookreviver.domain.enums import LabelStyle
from bookreviver.domain.page_label_rules import BookLabeling, PageLabelRule, PrintedNumber
from tests.adapters.imaging.samples import PdfPage, ScanImage, write_pdf

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.ports.imaging import PageLabelWriter

pytestmark = pytest.mark.anyio

PAGES: int = 4
SCAN_SIZE_PX: tuple[int, int] = (300, 400)
XHTML: str = 'http://www.w3.org/1999/xhtml'
EPUB_TYPE: str = '{http://www.idpf.org/2007/ops}type'
NAVIGATION_DOCUMENT: str = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    f'<html xmlns="{XHTML}" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head>'
    '<body><nav epub:type="toc"><ol><li><a href="page-0001.xhtml">Start</a></li></ol></nav></body></html>'
)

PREFACE_AND_TEXT = BookLabeling(
    rules=[
        PageLabelRule(first_index=0, style=LabelStyle.ROMAN_LOWER),
        PageLabelRule(first_index=2, style=LabelStyle.ARABIC),
    ],
    printed=[PrintedNumber(index=0, label='i'), PrintedNumber(index=1, label='ii'), PrintedNumber(index=2, label='1')],
)
LETTERS = BookLabeling(
    rules=[PageLabelRule(first_index=0, style=LabelStyle.ALPHA_LOWER)], printed=[PrintedNumber(index=3, label='d')]
)
UNNUMBERED = BookLabeling()


class FormatAdapter(NamedTuple):
    """An adapter of the port with what a test needs to give it a target and to see what it wrote.

    :ivar make: Builds the adapter.
    :ivar target: Makes the file the adapter changes, in a directory.
    :ivar kept: Reads what the adapter kept in the target, as a value that ``expected`` can be compared with.
    :ivar expected: Picks from a numbering the part the format keeps, in the form ``kept`` reads.
    """

    make: Callable[[], PageLabelWriter]
    target: Callable[[Path], Path]
    kept: Callable[[PageLabelWriter, Path], Any]
    expected: Callable[[BookLabeling], Any]


def _pdf_target(directory: Path) -> Path:
    """Write a PDF of four pages.

    :param directory: Directory to write in.
    :type directory: Path
    :returns: Path of the file.
    :rtype: Path
    """
    pages = [PdfPage(images=[ScanImage(mode='L', size_px=SCAN_SIZE_PX, image_format='JPEG')])] * PAGES
    return write_pdf(directory / 'book.pdf', pages=pages)


def _pdf_kept(_writer: PageLabelWriter, target: Path) -> list[PageLabelRule]:
    """Read the page label rules of a PDF.

    :param _writer: The adapter, which the file alone answers for.
    :type _writer: PageLabelWriter
    :param target: The PDF.
    :type target: Path
    :returns: The rules in the file.
    :rtype: list[PageLabelRule]
    """
    with pymupdf.open(target) as document:
        return read_label_rules(document.get_page_labels(), page_count=document.page_count)


def _epub_target(directory: Path) -> Path:
    """Write a navigation document that has a table of contents.

    :param directory: Directory to write in.
    :type directory: Path
    :returns: Path of the file.
    :rtype: Path
    """
    path = directory / 'nav.xhtml'
    path.write_text(NAVIGATION_DOCUMENT, encoding='utf-8')
    return path


def _epub_kept(_writer: PageLabelWriter, target: Path) -> list[str]:
    """Read the labels of the page list of a navigation document.

    :param _writer: The adapter, which the file alone answers for.
    :type _writer: PageLabelWriter
    :param target: The navigation document.
    :type target: Path
    :returns: The text of every link of the page list, in order, and nothing when there is none.
    :rtype: list[str]
    """
    return [
        link.text or ''
        for nav in parse(target).getroot().iter(f'{{{XHTML}}}nav')
        if nav.get(EPUB_TYPE) == 'page-list'
        for link in nav.iter(f'{{{XHTML}}}a')
    ]


def _memory_kept(writer: PageLabelWriter, target: Path) -> BookLabeling:
    """Read what the writer in memory remembers for a target.

    :param writer: The adapter in memory.
    :type writer: PageLabelWriter
    :param target: The path written to.
    :type target: Path
    :returns: The numbering written last.
    :rtype: BookLabeling
    """
    assert isinstance(writer, MemoryPageLabelWriter)
    return writer.written[target]


FORMAT_ADAPTERS: dict[str, FormatAdapter] = {
    'pdf': FormatAdapter(PdfLabelWriter, _pdf_target, _pdf_kept, lambda labeling: list(labeling.rules)),
    'epub': FormatAdapter(
        EpubPageList, _epub_target, _epub_kept, lambda labeling: [number.label for number in labeling.printed]
    ),
    'memory': FormatAdapter(
        MemoryPageLabelWriter, lambda directory: directory / 'out', _memory_kept, lambda labeling: labeling
    ),
}


@pytest.fixture(params=list(FORMAT_ADAPTERS), ids=str)
def fx_format(request: pytest.FixtureRequest) -> FormatAdapter:
    """Return each adapter of the PageLabelWriter port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` names the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, with the means to try it.
    :rtype: FormatAdapter
    """
    return FORMAT_ADAPTERS[request.param]


class TestWrite:
    """Contract of PageLabelWriter.write()."""

    async def test_what_the_format_keeps_of_the_numbering_can_be_read_back(
        self, fx_format: FormatAdapter, tmp_path: Path
    ) -> None:
        """Verify the target holds the part of the numbering that the format keeps.

        :param fx_format: The adapter under test.
        :type fx_format: FormatAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        writer, target = fx_format.make(), fx_format.target(tmp_path)

        await writer.write(target, PREFACE_AND_TEXT)

        assert fx_format.kept(writer, target) == fx_format.expected(PREFACE_AND_TEXT)

    async def test_a_second_write_replaces_the_first(self, fx_format: FormatAdapter, tmp_path: Path) -> None:
        """Verify nothing of an earlier numbering is left beside the new one.

        :param fx_format: The adapter under test.
        :type fx_format: FormatAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        writer, target = fx_format.make(), fx_format.target(tmp_path)
        await writer.write(target, PREFACE_AND_TEXT)

        await writer.write(target, LETTERS)

        assert fx_format.kept(writer, target) == fx_format.expected(LETTERS)

    async def test_a_book_without_numbers_leaves_none_in_the_target(
        self, fx_format: FormatAdapter, tmp_path: Path
    ) -> None:
        """Verify an empty numbering clears what the target held.

        :param fx_format: The adapter under test.
        :type fx_format: FormatAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        writer, target = fx_format.make(), fx_format.target(tmp_path)
        await writer.write(target, PREFACE_AND_TEXT)

        await writer.write(target, UNNUMBERED)

        assert fx_format.kept(writer, target) == fx_format.expected(UNNUMBERED)
