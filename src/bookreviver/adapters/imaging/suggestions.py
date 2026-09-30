"""Builds the book description a source suggests from the metadata of its file.

Three kinds of metadata carry description values: the document information dictionary of a PDF, its XMP packet, and
the pairs ``djvused -e print-meta`` prints for a DjVu document. ``SuggestionBuilder`` reads each with a method of its
own, and ``merge`` combines the suggestions of one file by priority, taking the first non-empty value of every field.
For a PDF the XMP packet comes first, because it lists several creators where the information dictionary holds one
string. For a DjVu document the BibTeX keys, written in lower case, come first, because they are closer to a
bibliographic description than the capitalised DocInfo keys.

A suggestion only ever fills an empty field of the description, so the rules err on the side of saying nothing:

- The author string of the information dictionary is split at semicolons only, since a comma occurs inside a name such
  as "Ивановъ, Н. Н.". Keywords are split at commas and semicolons.
- An identifier is kept only when it is a valid ISBN or an ``http`` or ``https`` address.
- A language tag is kept only when its primary subtag is an ISO 639-3 code, or an ISO 639-1 code this module can map.
- No date from a file becomes a year of publication, except the DjVu key ``year``. ``creationDate``, ``modDate`` and
  ``dc:date`` describe the file, and for a scan they are the day it was made, which would fill the field with a year
  that looks right and is wrong.

An XMP packet comes from a file the user uploaded, so it is parsed with ``defusedxml``, which refuses entity
declarations and external references. A packet that is refused or is not XML gives an empty suggestion.
"""

import enum
import logging
import re
from typing import TYPE_CHECKING, Any, Final

from attrs import fields_dict
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from bookreviver.domain.enums import ContributorRole, IdentifierScheme
from bookreviver.domain.errors import InvalidIdentifierError
from bookreviver.domain.values import BookIdentifier, Contributor, MetadataSuggestion

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from xml.etree.ElementTree import Element

logger = logging.getLogger(__name__)

# Namespaces of the XMP elements read: Dublin Core, RDF, and the ``xml:lang`` attribute of an alternative
DC_NAMESPACE: Final = 'http://purl.org/dc/elements/1.1/'
RDF_NAMESPACE: Final = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
LANG_ATTRIBUTE: Final = '{http://www.w3.org/XML/1998/namespace}lang'
RDF_ITEM: Final = f'.//{{{RDF_NAMESPACE}}}li'
# The language alternative that XMP marks as the one to show when no other matches
DEFAULT_LANGUAGE_ALTERNATIVE: Final = 'x-default'
AUTHOR_SEPARATOR: Final = re.compile(r';')
KEYWORD_SEPARATOR: Final = re.compile(r'[,;]')
# A prefix that names an ISBN in a Dublin Core identifier, such as ``urn:isbn:`` and ``ISBN ``
ISBN_PREFIX: Final = re.compile(r'^(?:urn:)?isbn[:\s-]*', re.IGNORECASE)
PUBLISHER_SEPARATOR: Final = '; '
ISO_639_3_LENGTH: Final = 3
ISO_639_1_LENGTH: Final = 2
# ISO 639-3 codes of the languages of old books by their ISO 639-1 codes, which tags in XMP usually use
ISO_639_3_BY_ISO_639_1: Final[Mapping[str, str]] = {
    'ar': 'ara',
    'be': 'bel',
    'bg': 'bul',
    'bs': 'bos',
    'cs': 'ces',
    'cu': 'chu',
    'da': 'dan',
    'de': 'deu',
    'el': 'ell',
    'en': 'eng',
    'es': 'spa',
    'et': 'est',
    'fa': 'fas',
    'fi': 'fin',
    'fr': 'fra',
    'he': 'heb',
    'hr': 'hrv',
    'hu': 'hun',
    'hy': 'hye',
    'is': 'isl',
    'it': 'ita',
    'ka': 'kat',
    'la': 'lat',
    'lt': 'lit',
    'lv': 'lav',
    'mk': 'mkd',
    'nl': 'nld',
    'no': 'nor',
    'pl': 'pol',
    'pt': 'por',
    'ro': 'ron',
    'ru': 'rus',
    'sk': 'slk',
    'sl': 'slv',
    'sr': 'srp',
    'sv': 'swe',
    'tr': 'tur',
    'tt': 'tat',
    'uk': 'ukr',
    'yi': 'yid',
}


class DocInfoKey(enum.StrEnum):
    """Keys of the PDF document information that hold description values, as PyMuPDF spells them."""

    TITLE = 'title'
    AUTHOR = 'author'
    SUBJECT = 'subject'
    KEYWORDS = 'keywords'


class XmpElement(enum.StrEnum):
    """Local names of the Dublin Core elements of an XMP packet that hold description values."""

    TITLE = 'title'
    CREATOR = 'creator'
    CONTRIBUTOR = 'contributor'
    PUBLISHER = 'publisher'
    LANGUAGE = 'language'
    IDENTIFIER = 'identifier'
    SUBJECT = 'subject'


class DjvuMetaKey(enum.StrEnum):
    """Keys of the DjVu document metadata that hold description values, as BibTeX spells them in lower case.

    The DocInfo spelling of a key is the same word with a capital, such as ``Title``.
    """

    TITLE = 'title'
    AUTHOR = 'author'
    EDITOR = 'editor'
    PUBLISHER = 'publisher'
    YEAR = 'year'


class SuggestionBuilder:
    """Reads description values from the metadata of a file and merges what several readings found.

    The builder keeps no state: every method takes the metadata it reads and returns a ``MetadataSuggestion``, so the
    PDF format and the DjVu format share it without sharing an instance.
    """

    @classmethod
    def from_docinfo(cls, info: Mapping[str, str | None]) -> MetadataSuggestion:
        """Read the document information dictionary of a PDF.

        Only the title, the author, the subject and the keywords are read. The dates of the dictionary describe the
        file and never become a year of publication.

        :param info: The dictionary as PyMuPDF returns ``Document.metadata``, where a missing value may be None.
        :type info: Mapping[str, str | None]
        :returns: The title, the authors split at semicolons, and the subject and the keywords as subjects.
        :rtype: MetadataSuggestion
        """
        keywords = KEYWORD_SEPARATOR.split(info.get(DocInfoKey.KEYWORDS) or '')
        return MetadataSuggestion(
            title=(info.get(DocInfoKey.TITLE) or '').strip(),
            contributors=_people(info.get(DocInfoKey.AUTHOR) or '', ContributorRole.AUTHOR),
            subjects=_distinct([info.get(DocInfoKey.SUBJECT) or '', *keywords]),
        )

    @classmethod
    def from_xmp(cls, xml: str) -> MetadataSuggestion:
        """Read the Dublin Core elements of an XMP packet.

        :param xml: The packet as ``Document.get_xml_metadata`` returns it, or an empty string when there is none.
        :type xml: str
        :returns: The suggestion the packet gives, or an empty suggestion when there is no packet, when it is not XML,
                  or when it declares entities or external references.
        :rtype: MetadataSuggestion
        """
        if not xml.strip():
            return MetadataSuggestion()
        try:
            root = fromstring(xml, forbid_dtd=True)
        except DefusedXmlException, ParseError:
            logger.warning('An XMP packet was ignored because it is not plain XML.')
            return MetadataSuggestion()

        found = {element: _xmp_values(root, element) for element in XmpElement}
        return MetadataSuggestion(
            title=next(iter(found[XmpElement.TITLE]), ''),
            contributors=(
                *(Contributor(name=name, role=ContributorRole.AUTHOR) for name in _distinct(found[XmpElement.CREATOR])),
                *(
                    Contributor(name=name, role=ContributorRole.CONTRIBUTOR)
                    for name in _distinct(found[XmpElement.CONTRIBUTOR])
                ),
            ),
            publisher=PUBLISHER_SEPARATOR.join(_distinct(found[XmpElement.PUBLISHER])),
            languages=_distinct(_language(tag) for tag in found[XmpElement.LANGUAGE]),
            identifiers=_identifiers(found[XmpElement.IDENTIFIER]),
            subjects=_distinct(found[XmpElement.SUBJECT]),
        )

    @classmethod
    def from_djvu_meta(cls, meta: Mapping[str, str]) -> MetadataSuggestion:
        """Read the pairs ``djvused -e print-meta`` printed for a DjVu document.

        BibTeX keys are lower case and DocInfo keys start with a capital. The BibTeX reading wins, and the DocInfo
        reading fills what it lacks.

        :param meta: The pairs as the file spells their keys.
        :type meta: Mapping[str, str]
        :returns: The title, the authors and editors, the publisher and the year the metadata states.
        :rtype: MetadataSuggestion
        """
        return cls.merge(_djvu_reading(meta, str), _djvu_reading(meta, str.capitalize))

    @staticmethod
    def merge(*suggestions: MetadataSuggestion) -> MetadataSuggestion:
        """Combine the suggestions of one file, taking the first non-empty value of every field.

        :param suggestions: Suggestions in order of priority, the most trusted first.
        :type suggestions: MetadataSuggestion
        :returns: A suggestion whose every field is that of the first suggestion that has a value for it.
        :rtype: MetadataSuggestion
        """
        merged: dict[str, Any] = {
            name: next((value for found in suggestions if (value := getattr(found, name))), field.default)
            for name, field in fields_dict(MetadataSuggestion).items()
        }
        return MetadataSuggestion(**merged)


def _people(names: str, role: ContributorRole) -> tuple[Contributor, ...]:
    """Split a string of people at semicolons into contributors of one role.

    :param names: The people as a file stored them, separated by semicolons.
    :type names: str
    :param role: Role every person of the string has.
    :type role: ContributorRole
    :returns: A contributor for each distinct non-empty name, in order.
    :rtype: tuple[Contributor, ...]
    """
    return tuple(Contributor(name=name, role=role) for name in _distinct(AUTHOR_SEPARATOR.split(names)))


def _distinct(items: Iterable[str]) -> tuple[str, ...]:
    """Strip the items, drop the empty ones and the repeats, and keep the order.

    :param items: Texts in any spelling of whitespace.
    :type items: Iterable[str]
    :returns: The distinct non-empty stripped texts in their first-seen order.
    :rtype: tuple[str, ...]
    """
    return tuple(dict.fromkeys(stripped for item in items if (stripped := item.strip())))


def _djvu_reading(meta: Mapping[str, str], spell: Callable[[str], str]) -> MetadataSuggestion:
    """Read the DjVu metadata keys in one spelling.

    :param meta: The pairs as the file spells their keys.
    :type meta: Mapping[str, str]
    :param spell: Turns a BibTeX key into the spelling to read, such as ``str.capitalize`` for DocInfo.
    :type spell: Callable[[str], str]
    :returns: The suggestion the keys of that spelling give.
    :rtype: MetadataSuggestion
    """
    read = {key: (meta.get(spell(key)) or '').strip() for key in DjvuMetaKey}
    return MetadataSuggestion(
        title=read[DjvuMetaKey.TITLE],
        contributors=(
            *_people(read[DjvuMetaKey.AUTHOR], ContributorRole.AUTHOR),
            *_people(read[DjvuMetaKey.EDITOR], ContributorRole.EDITOR),
        ),
        publisher=read[DjvuMetaKey.PUBLISHER],
        publication_year=read[DjvuMetaKey.YEAR],
    )


def _xmp_values(root: Element, name: XmpElement) -> list[str]:
    """Return the texts of a Dublin Core element wherever it stands in the packet.

    :param root: Root of the parsed packet.
    :type root: Element
    :param name: Local name of the Dublin Core element.
    :type name: XmpElement
    :returns: The non-empty texts of the list items of the element, those marked ``x-default`` first, or the text of
              the element itself when it has no list items.
    :rtype: list[str]
    """
    texts: list[str] = []
    for element in root.iter(f'{{{DC_NAMESPACE}}}{name}'):
        items = sorted(
            element.findall(RDF_ITEM), key=lambda item: item.get(LANG_ATTRIBUTE) != DEFAULT_LANGUAGE_ALTERNATIVE
        )
        texts.extend(''.join(item.itertext()) for item in items)
        if not items:
            texts.append(''.join(element.itertext()))
    return [text.strip() for text in texts if text.strip()]


def _language(tag: str) -> str:
    """Turn a language tag into an ISO 639-3 code.

    :param tag: A tag such as ``ru``, ``ru-RU`` or ``rus``.
    :type tag: str
    :returns: The code of the primary subtag, or an empty string when it is neither an ISO 639-3 code nor an ISO 639-1
              code the module maps.
    :rtype: str
    """
    primary = tag.strip().lower().replace('_', '-').partition('-')[0]
    if len(primary) == ISO_639_3_LENGTH and primary.isascii() and primary.isalpha():
        return primary
    return ISO_639_3_BY_ISO_639_1.get(primary, '') if len(primary) == ISO_639_1_LENGTH else ''


def _identifiers(values: Iterable[str]) -> tuple[BookIdentifier, ...]:
    """Keep the values that are a valid ISBN or an ``http`` or ``https`` address.

    :param values: Values of ``dc:identifier``, such as ``urn:isbn:0306406152`` or an address.
    :type values: Iterable[str]
    :returns: The distinct identifiers in normalized form, in order.
    :rtype: tuple[BookIdentifier, ...]
    """
    found: dict[BookIdentifier, None] = {}
    for value in values:
        for scheme, raw in ((IdentifierScheme.ISBN, ISBN_PREFIX.sub('', value)), (IdentifierScheme.URL, value)):
            try:
                found[BookIdentifier.parse(scheme, raw)] = None
            except InvalidIdentifierError:
                continue
            break
    return tuple(found)
