"""Tests for the page list of an EPUB navigation document."""

from typing import TYPE_CHECKING

import pytest
from defusedxml.ElementTree import parse
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.imaging.epub_page_list import EPUB_NAMESPACE, PAGE_LIST_TYPE, XHTML_NAMESPACE, EpubPageList
from bookreviver.domain.page_label_rules import BookLabeling, PrintedNumber

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.anyio

NAV_NAME: str = 'nav.xhtml'
NAVIGATION_DOCUMENT: str = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
    '<head><title>Contents</title></head>'
    '<body><nav epub:type="toc"><ol><li><a href="page-0001.xhtml">Start</a></li></ol></nav></body></html>'
)
BODYLESS_DOCUMENT: str = (
    "<?xml version='1.0' encoding='utf-8'?>\n"
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Contents</title></head></html>'
)
TYPE_ATTRIBUTE: str = f'{{{EPUB_NAMESPACE}}}type'


def _navs(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Read the ``nav`` elements of a navigation document, each with the links in it.

    :param path: The navigation document.
    :type path: Path
    :returns: The links, as the address and the text of each, by the epub type of the ``nav`` they are in.
    :rtype: dict[str, list[tuple[str, str]]]
    """
    body = parse(path).getroot().find(f'{{{XHTML_NAMESPACE}}}body')
    assert body is not None
    return {
        nav.get(TYPE_ATTRIBUTE, ''): [
            (link.get('href', ''), link.text or '') for link in nav.iter(f'{{{XHTML_NAMESPACE}}}a')
        ]
        for nav in body.findall(f'{{{XHTML_NAMESPACE}}}nav')
    }


def _labeling(*labels: tuple[int, str]) -> BookLabeling:
    """Build a numbering that prints the given labels.

    :param labels: Position and label of each printed number.
    :type labels: tuple[int, str]
    :returns: The numbering, without rules.
    :rtype: BookLabeling
    """
    return BookLabeling(printed=[PrintedNumber(index=index, label=label) for index, label in labels])


class TestEpubPageList:
    """Tests for EpubPageList."""

    async def test_the_printed_numbers_become_a_page_list_that_links_their_pages(self, tmp_path: Path) -> None:
        """Verify each number is a link to the content document of its page, counted from 1, beside the contents.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / NAV_NAME
        path.write_text(NAVIGATION_DOCUMENT, encoding='utf-8')

        await EpubPageList().write(path, _labeling((0, 'i'), (2, '1'), (3, 'Plate I')))

        navs = _navs(path)
        expect(
            navs[PAGE_LIST_TYPE] == [('page-0001.xhtml', 'i'), ('page-0003.xhtml', '1'), ('page-0004.xhtml', 'Plate I')]
        )
        expect(navs['toc'] == [('page-0001.xhtml', 'Start')])
        assert_expectations()

    async def test_the_page_list_is_hidden(self, tmp_path: Path) -> None:
        """Verify the list is marked hidden, since a reading system builds its own navigation from it.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / NAV_NAME
        path.write_text(NAVIGATION_DOCUMENT, encoding='utf-8')

        await EpubPageList().write(path, _labeling((0, '1')))

        body = parse(path).getroot().find(f'{{{XHTML_NAMESPACE}}}body')
        assert body is not None
        [page_list] = [nav for nav in body if nav.get(TYPE_ATTRIBUTE) == PAGE_LIST_TYPE]
        assert page_list.get('hidden') == 'hidden'

    async def test_a_second_write_replaces_the_first_list(self, tmp_path: Path) -> None:
        """Verify there is one page list after two writes, with the numbers of the last.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / NAV_NAME
        path.write_text(NAVIGATION_DOCUMENT, encoding='utf-8')
        writer = EpubPageList()

        await writer.write(path, _labeling((0, 'i')))
        await writer.write(path, _labeling((1, '7')))

        navs = _navs(path)
        expect(navs[PAGE_LIST_TYPE] == [('page-0002.xhtml', '7')])
        expect(len(navs) == 2)
        assert_expectations()

    async def test_a_book_that_prints_no_number_has_no_page_list(self, tmp_path: Path) -> None:
        """Verify an earlier list is removed and none is added, since a list must hold at least one entry.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / NAV_NAME
        path.write_text(NAVIGATION_DOCUMENT, encoding='utf-8')
        writer = EpubPageList()
        await writer.write(path, _labeling((0, 'i')))

        await writer.write(path, BookLabeling())

        assert list(_navs(path)) == ['toc']

    async def test_the_address_of_a_page_follows_the_template(self, tmp_path: Path) -> None:
        """Verify the template names the content document of the page by its position.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / NAV_NAME
        path.write_text(NAVIGATION_DOCUMENT, encoding='utf-8')

        await EpubPageList(href_template='text/p{position}.xhtml').write(path, _labeling((4, 'v')))

        assert _navs(path)[PAGE_LIST_TYPE] == [('text/p5.xhtml', 'v')]

    async def test_a_document_without_a_body_is_refused(self, tmp_path: Path) -> None:
        """Verify a document that has no place for a list raises instead of being written without one.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = tmp_path / NAV_NAME
        path.write_text(BODYLESS_DOCUMENT, encoding='utf-8')

        with pytest.raises(ValueError, match='no body'):
            await EpubPageList().write(path, _labeling((0, '1')))
