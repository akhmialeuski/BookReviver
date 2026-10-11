"""The page list of an EPUB 3 navigation document, which names the numbers printed on the pages of the paper book.

A reading system shows these numbers beside the text and jumps to a page by its number. The list is a ``nav`` element
of type ``page-list`` in the navigation document, an ordered list of links, one for each page that prints a number.
The pages of the book are image pages, one content document each, so a link names the content document of its page.
The navigation document is a file of the book that the user may have written, so it is read with ``defusedxml``.
"""

from typing import TYPE_CHECKING, override
from xml.etree import ElementTree as ET

from anyio import to_thread
from defusedxml.ElementTree import parse

from bookreviver.ports.imaging import PageLabelWriter

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.page_label_rules import BookLabeling, PrintedNumber

XHTML_NAMESPACE: str = 'http://www.w3.org/1999/xhtml'
EPUB_NAMESPACE: str = 'http://www.idpf.org/2007/ops'
PAGE_LIST_TYPE: str = 'page-list'
NAV_ELEMENT: str = 'nav'
HIDDEN_ATTRIBUTE: str = 'hidden'
# Name of the content document of a page, from its position in the book counted from 1
DEFAULT_HREF_TEMPLATE: str = 'page-{position:04d}.xhtml'

ET.register_namespace('', XHTML_NAMESPACE)
ET.register_namespace('epub', EPUB_NAMESPACE)


def _xhtml(name: str) -> str:
    """Qualify an element name with the namespace of XHTML.

    :param name: Local name of the element.
    :type name: str
    :returns: The name in the form ElementTree keeps.
    :rtype: str
    """
    return f'{{{XHTML_NAMESPACE}}}{name}'


class EpubPageList(PageLabelWriter):
    """Writes the printed numbers of a book as the page list of the navigation document of its EPUB."""

    def __init__(self, *, href_template: str = DEFAULT_HREF_TEMPLATE) -> None:
        """Link every number to the content document its page is in.

        :param href_template: Address of the content document of a page, relative to the navigation document, in which
                              ``{position}`` is the position of the page among the pages of the book, from 1.
        :type href_template: str
        """
        self._href_template = href_template

    @override
    async def write(self, target: Path, labeling: BookLabeling) -> None:
        """Replace the page list of a navigation document with the numbers the pages of the book print.

        The list is hidden, since a reading system builds its own page navigation from it, and it is left out when no
        page prints a number. The rest of the document stays as it is.

        :param target: The navigation document, an XHTML file with a body.
        :type target: Path
        :param labeling: The numbering of the book, of which the printed numbers are written.
        :type labeling: BookLabeling
        :raises ValueError: If the document has no body.
        :raises ParseError: If the document is not well-formed XML.
        """
        await to_thread.run_sync(self._write, target, labeling.printed)

    def _write(self, target: Path, printed: Sequence[PrintedNumber]) -> None:
        """Write the page list into the document, which blocks, so the caller runs it in a worker thread.

        :param target: The navigation document.
        :type target: Path
        :param printed: The numbers the pages print, in the order of the pages.
        :type printed: Sequence[PrintedNumber]
        :raises ValueError: If the document has no body.
        """
        tree = parse(target)
        body = tree.find(_xhtml('body'))
        if body is None:
            err_msg = f'{target.name} has no body to hold a page list.'
            raise ValueError(err_msg)
        for nav in body.findall(_xhtml(NAV_ELEMENT)):
            if PAGE_LIST_TYPE in nav.get(f'{{{EPUB_NAMESPACE}}}type', '').split():
                body.remove(nav)
        if printed:
            attributes = {f'{{{EPUB_NAMESPACE}}}type': PAGE_LIST_TYPE, HIDDEN_ATTRIBUTE: HIDDEN_ATTRIBUTE}
            nav = ET.SubElement(body, _xhtml(NAV_ELEMENT), attributes)
            items = ET.SubElement(nav, _xhtml('ol'))
            for number in printed:
                item = ET.SubElement(items, _xhtml('li'))
                link = ET.SubElement(item, _xhtml('a'), {'href': self._href_template.format(position=number.index + 1)})
                link.text = number.label
        tree.write(target, encoding='utf-8', xml_declaration=True)
