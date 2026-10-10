"""The page labels of a PDF file, read as rules of the domain and written back from them with PyMuPDF.

A PDF keeps its page numbers as label rules: each names the page it starts at, a style letter, a prefix and the number
of its first page. ``PageLabelRule`` is the same shape with the style as a ``LabelStyle``, so reading and writing only
spell the letters. ``Document.get_page_labels`` rebuilds each rule from the printed form of its dictionary, which
shows a prefix with characters outside ASCII as a hexadecimal string, so ``read_label_rules`` decodes that form. The
other direction, ``Document.set_page_labels``, pastes the prefix into the rule between parentheses without escaping it,
so ``pdf_text`` writes it as a literal string a PDF reader takes back exactly.
"""

import enum
import re
from operator import itemgetter
from typing import TYPE_CHECKING, Any, override

import pymupdf
from anyio import to_thread

from bookreviver.domain.enums import LabelStyle
from bookreviver.domain.page_label_rules import PageLabelRule
from bookreviver.ports.imaging import PageLabelWriter

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from bookreviver.domain.page_label_rules import BookLabeling

# PyMuPDF prints a text string with characters outside ASCII as hexadecimal digits in angle brackets
HEX_STRING: re.Pattern[str] = re.compile(r'<([0-9a-fA-F]*)>')
UTF16_BOM: bytes = b'\xfe\xff'
# The printable ASCII characters a literal string keeps as they are, except the three that need a backslash
PRINTABLE_ASCII: range = range(0x20, 0x7F)
LITERAL_SPECIALS: frozenset[int] = frozenset(b'\\()')


class PdfLabelKey(enum.StrEnum):
    """Keys of the dictionaries ``Document.get_page_labels`` returns and ``Document.set_page_labels`` takes."""

    START_PAGE = 'startpage'
    PREFIX = 'prefix'
    STYLE = 'style'
    FIRST_NUMBER = 'firstpagenum'


class PdfLabelStyle(enum.StrEnum):
    """The letters a PDF page label rule names its numbering style with, which are the ones of PDF 32000."""

    DECIMAL = 'D'
    ROMAN_UPPER = 'R'
    ROMAN_LOWER = 'r'
    ALPHA_UPPER = 'A'
    ALPHA_LOWER = 'a'
    NONE = ''


LABEL_STYLES: Mapping[PdfLabelStyle, LabelStyle] = {
    PdfLabelStyle.DECIMAL: LabelStyle.ARABIC,
    PdfLabelStyle.ROMAN_UPPER: LabelStyle.ROMAN_UPPER,
    PdfLabelStyle.ROMAN_LOWER: LabelStyle.ROMAN_LOWER,
    PdfLabelStyle.ALPHA_UPPER: LabelStyle.ALPHA_UPPER,
    PdfLabelStyle.ALPHA_LOWER: LabelStyle.ALPHA_LOWER,
    PdfLabelStyle.NONE: LabelStyle.NONE,
}
PDF_STYLES: Mapping[LabelStyle, PdfLabelStyle] = {style: letter for letter, style in LABEL_STYLES.items()}


def read_label_rules(entries: Sequence[Mapping[str, Any]], *, page_count: int) -> list[PageLabelRule]:
    """Turn the label rules PyMuPDF reports into rules of the domain, in page order.

    A rule that starts past the last page names no page, and a style letter nobody defines writes the prefix alone,
    as a reader that does not know the letter would show it.

    :param entries: Dictionaries of ``Document.get_page_labels``, in any order.
    :type entries: Sequence[Mapping[str, Any]]
    :param page_count: Number of pages of the document.
    :type page_count: int
    :returns: The rules that start at a page of the document, by the page they start at, one per page at most.
    :rtype: list[PageLabelRule]
    """
    rules: dict[int, PageLabelRule] = {}
    for entry in sorted(entries, key=itemgetter(PdfLabelKey.START_PAGE)):
        start = entry[PdfLabelKey.START_PAGE]
        if not 0 <= start < page_count:
            continue
        rules[start] = PageLabelRule(
            first_index=start,
            style=LABEL_STYLES.get(entry.get(PdfLabelKey.STYLE, ''), LabelStyle.NONE),
            prefix=_prefix_text(entry.get(PdfLabelKey.PREFIX, '')),
            start=max(0, entry.get(PdfLabelKey.FIRST_NUMBER, 1)),
        )
    return list(rules.values())


def _prefix_text(prefix: str) -> str:
    """Read the prefix of a rule, undoing the hexadecimal form PyMuPDF shows a string with non-ASCII characters in.

    :param prefix: Prefix as ``Document.get_page_labels`` returns it.
    :type prefix: str
    :returns: The text of the prefix; a hexadecimal string is decoded as UTF-16 when it opens with the byte order mark,
              and as Latin-1 otherwise, which is the PDF text encoding for the characters it has.
    :rtype: str
    """
    if (found := HEX_STRING.fullmatch(prefix)) is None:
        return prefix
    digits = found.group(1)
    raw = bytes.fromhex(digits if len(digits) % 2 == 0 else f'{digits}0')
    return raw.decode('utf-16') if raw.startswith(UTF16_BOM) else raw.decode('latin-1')


def pdf_text(text: str) -> str:
    """Write text as the inside of a PDF literal string, so that ``/P(…)`` reads back as the same text.

    :param text: The text of a prefix.
    :type text: str
    :returns: Printable ASCII with the backslash and the parentheses escaped, or, when the text has anything else, its
              UTF-16 form with the byte order mark, every byte written as an octal escape.
    :rtype: str
    """
    raw = text.encode('ascii') if text.isascii() else UTF16_BOM + text.encode('utf-16-be')
    return ''.join(
        f'\\{byte:03o}' if byte in LITERAL_SPECIALS or byte not in PRINTABLE_ASCII else chr(byte) for byte in raw
    )


def pdf_label_entries(rules: Sequence[PageLabelRule]) -> list[dict[str, Any]]:
    """Turn the rules of the domain into the dictionaries ``Document.set_page_labels`` takes.

    :param rules: Rules in page order.
    :type rules: Sequence[PageLabelRule]
    :returns: One dictionary for each rule, with its prefix escaped for the rule string PyMuPDF builds.
    :rtype: list[dict[str, Any]]
    """
    return [
        {
            PdfLabelKey.START_PAGE: rule.first_index,
            PdfLabelKey.PREFIX: pdf_text(rule.prefix),
            PdfLabelKey.STYLE: PDF_STYLES[rule.style].value,
            PdfLabelKey.FIRST_NUMBER: rule.start,
        }
        for rule in rules
    ]


class PdfLabelWriter(PageLabelWriter):
    """Writes the page label rules of a book into a PDF file that has its pages."""

    @override
    async def write(self, target: Path, labeling: BookLabeling) -> None:
        """Replace the page label rules of a PDF file with the rules of the book, saving the file in place.

        :param target: The PDF file whose pages are the pages of the book, in the same order.
        :type target: Path
        :param labeling: The numbering of the book, of which the rules are written.
        :type labeling: BookLabeling
        :raises ValueError: If a rule starts past the last page of the file.
        """
        await to_thread.run_sync(self._write, target, labeling.rules)

    @staticmethod
    def _write(target: Path, rules: Sequence[PageLabelRule]) -> None:
        """Write the rules into the file, which blocks, so the caller runs it in a worker thread.

        :param target: The PDF file.
        :type target: Path
        :param rules: Rules in page order.
        :type rules: Sequence[PageLabelRule]
        :raises ValueError: If a rule starts past the last page of the file.
        """
        with pymupdf.open(target) as document:
            if rules and rules[-1].first_index >= document.page_count:
                err_msg = (
                    f'{target.name} has {document.page_count} pages, so no rule starts at {rules[-1].first_index}.'
                )
                raise ValueError(err_msg)
            document.set_page_labels(pdf_label_entries(rules))
            document.saveIncr()
