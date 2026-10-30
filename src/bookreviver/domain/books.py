"""A printed book as it is described and catalogued: its description, its identifiers and its record."""

import re
from itertools import cycle
from typing import TYPE_CHECKING, ClassVar, Final, Self
from urllib.parse import urlsplit

from attrs import evolve, field, fields_dict, frozen, validators

from bookreviver.domain.errors import InvalidIdentifierError
from bookreviver.domain.images import ImagePolicy
from bookreviver.domain.labels import LabeledStrEnum

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from attrs import Attribute


# An ISBN without hyphens: ten characters whose last may be the check digit ``X``, or thirteen digits
ISBN_10: Final = re.compile(r'[0-9]{9}[0-9X]')
ISBN_13: Final = re.compile(r'[0-9]{13}')
ISBN_10_MODULUS: Final = 11
ISBN_13_MODULUS: Final = 10
# The check digit ``X`` of an ISBN-10 stands for ten
ISBN_10_X_VALUE: Final = 10
# Weights of the digits of an ISBN-13, which alternate
ISBN_13_WEIGHTS: Final = (1, 3)
# Digits the serial number of an LCCN is padded to after its hyphen
LCCN_SERIAL_LENGTH: Final = 6
# A normalized LCCN has up to four characters of prefix and year, and ends with eight digits
LCCN_NORMALIZED: Final = re.compile(r'[a-z0-9]{0,4}[0-9]{8}')
OCLC_NUMBER: Final = re.compile(r'[0-9]+')
WHITESPACE: Final = re.compile(r'\s')
# Longest shelfmark and longest URL of a copy the domain accepts
SHELFMARK_MAX_LENGTH: Final = 300
URL_MAX_LENGTH: Final = 2_048
URL_SCHEMES: Final = frozenset({'http', 'https'})


class Orthography(LabeledStrEnum):
    """Spelling norm of the printed text."""

    UNKNOWN = 'unknown', 'Unknown'
    PRE_REFORM = 'pre-reform', 'Pre-reform'
    MODERN = 'modern', 'Modern'


class Script(LabeledStrEnum):
    """Writing system of the printed text, which is separate from its spelling norm."""

    UNKNOWN = 'unknown', 'Unknown'
    CYRILLIC = 'cyrillic', 'Cyrillic'
    LATIN = 'latin', 'Latin'
    MIXED = 'mixed', 'Mixed'


class RightsStatus(LabeledStrEnum):
    """Whether the result of the work on a book may be published."""

    UNKNOWN = 'unknown', 'Unknown'
    PUBLIC_DOMAIN = 'public-domain', 'Public domain'
    IN_COPYRIGHT = 'in-copyright', 'In copyright'


class ContributorRole(LabeledStrEnum):
    """Role of a person in the making of a book.

    The value of a member is its code in the `MARC Code List for Relators <https://www.loc.gov/marc/relators/>`_ and
    the label is the term of that list, so an export to library formats writes the codes without a translation table.
    """

    AUTHOR = 'aut', 'author'
    EDITOR = 'edt', 'editor'
    COMPILER = 'com', 'compiler'
    TRANSLATOR = 'trl', 'translator'
    ILLUSTRATOR = 'ill', 'illustrator'
    ENGRAVER = 'egr', 'engraver'
    LITHOGRAPHER = 'ltg', 'lithographer'
    PHOTOGRAPHER = 'pht', 'photographer'
    WRITER_OF_PREFACE = 'wpr', 'writer of preface'
    WRITER_OF_INTRODUCTION = 'win', 'writer of introduction'
    ANNOTATOR = 'ann', 'annotator'
    COMMENTATOR = 'cmm', 'commentator'
    DEDICATEE = 'dte', 'dedicatee'
    CONTRIBUTOR = 'ctb', 'contributor'
    OTHER = 'oth', 'other'


class IdentifierScheme(LabeledStrEnum):
    """Kind of number or address that identifies a book or one copy of it, each with its rule of writing."""

    ISBN = 'isbn', 'ISBN'
    OCLC = 'oclc', 'OCLC number'
    LCCN = 'lccn', 'LCCN'
    SHELFMARK = 'shelfmark', 'Shelfmark'
    URL = 'url', 'URL of a copy'

    def normalize(self, raw: str) -> str:
        """Return ``raw`` in the one form the scheme compares by, after checking it.

        The rules follow the standards of each scheme: an ISBN loses its hyphens and spaces and must carry a correct
        check digit, an OCLC number is digits, an LCCN is normalized as the Library of Congress describes, a shelfmark
        is free text, and a URL is an ``http`` or ``https`` address.

        :param raw: The identifier as a person wrote it or a file stored it.
        :type raw: str
        :returns: The identifier in normalized form.
        :rtype: str
        :raises InvalidIdentifierError: If the identifier breaks the rules of its scheme.
        """
        text = raw.strip()
        match self:
            case IdentifierScheme.ISBN:
                normalized = re.sub(r'[-\s]', '', text).upper()
                valid = self._has_isbn_check_digit(normalized)
            case IdentifierScheme.OCLC:
                normalized = text
                valid = OCLC_NUMBER.fullmatch(normalized) is not None
            case IdentifierScheme.LCCN:
                normalized = WHITESPACE.sub('', text).lower().partition('/')[0]
                head, hyphen, serial = normalized.partition('-')
                if hyphen:
                    normalized = head + serial.zfill(LCCN_SERIAL_LENGTH)
                valid = LCCN_NORMALIZED.fullmatch(normalized) is not None
            case IdentifierScheme.SHELFMARK:
                normalized = text
                valid = 0 < len(text) <= SHELFMARK_MAX_LENGTH
            case IdentifierScheme.URL:
                normalized = text
                valid = self._is_web_address(text)
        if not valid:
            err_msg = f'{raw!r} is not a valid {self.label}.'
            raise InvalidIdentifierError(err_msg)
        return normalized

    @staticmethod
    def _has_isbn_check_digit(isbn: str) -> bool:
        """Tell whether ``isbn`` is an ISBN-10 or an ISBN-13 whose weighted digit sum is a multiple of its modulus.

        :param isbn: The ISBN without hyphens and spaces, in upper case.
        :type isbn: str
        :returns: Whether it has the length, the digits and the check digit of an ISBN.
        :rtype: bool
        """
        if ISBN_10.fullmatch(isbn):
            values = [ISBN_10_X_VALUE if digit == 'X' else int(digit) for digit in isbn]
            weights = range(len(isbn), 0, -1)
            return sum(weight * value for weight, value in zip(weights, values, strict=True)) % ISBN_10_MODULUS == 0
        if ISBN_13.fullmatch(isbn):
            total = sum(weight * int(digit) for weight, digit in zip(cycle(ISBN_13_WEIGHTS), isbn, strict=False))
            return total % ISBN_13_MODULUS == 0
        return False

    @staticmethod
    def _is_web_address(text: str) -> bool:
        """Tell whether ``text`` is a single ``http`` or ``https`` address with a host.

        :param text: The address without surrounding whitespace.
        :type text: str
        :returns: Whether it is such an address.
        :rtype: bool
        """
        if not 0 < len(text) <= URL_MAX_LENGTH or WHITESPACE.search(text):
            return False
        try:
            parts = urlsplit(text)
        except ValueError:
            return False
        return parts.scheme.lower() in URL_SCHEMES and bool(parts.hostname)


class BookResource(LabeledStrEnum):
    """A part of a book a reader asks for, which a change of the book names so the reader reads only that part again.

    The place where the reader left the book is not one: it changes as the reader moves and is read once, when the
    book is opened.
    """

    RECORD = 'record', 'The description of the book'
    SOURCES = 'sources', 'The uploaded files of the book'
    SCANS = 'scans', 'The scans of the files'
    PAGES = 'pages', 'The pages, their order, their labels and their groups'
    VERSIONS = 'versions', 'The results of the pages'
    STAGES = 'stages', 'Where every page stands in every stage'
    SETTINGS = 'settings', 'The settings and the edits of the steps on the pages'
    HISTORY = 'history', 'The history of the steps on the pages'
    RECIPES = 'recipes', 'The recipes of the stages and the recipe profiles'


# The description field a suggestion never fills
TITLE_FIELD: str = 'title'


@frozen(kw_only=True)
class Contributor:
    """A person who took part in the making of a book, in the role the title page or a catalogue gives them.

    The name is kept as printed, with the spelling and the initials of the book, because a catalogue of old books is
    searched by that form. The position of a contributor in the list of the description records the order of the
    title page.

    :ivar name: Name as printed in the book.
    :ivar role: Role of the person, a code of the MARC list of relators.
    """

    name: str = field(validator=validators.min_len(1))
    role: ContributorRole


def _is_normalized(identifier: BookIdentifier, _attribute: Attribute[str], value: str) -> None:
    """Check that the value follows the rules of the scheme of its identifier and is already normalized.

    :param identifier: The identifier being built, whose scheme the value must follow.
    :type identifier: BookIdentifier
    :param _attribute: The attribute being validated, which the rule does not need.
    :type _attribute: Attribute[str]
    :param value: The value to check.
    :type value: str
    :raises InvalidIdentifierError: If the value breaks the rules of the scheme or is not normalized.
    """
    if (normalized := identifier.scheme.normalize(value)) != value:
        err_msg = f'{value!r} is not normalized; the normalized {identifier.scheme.label} is {normalized!r}.'
        raise InvalidIdentifierError(err_msg)


@frozen(kw_only=True)
class BookIdentifier:
    """A number or an address that identifies a book or one copy of it, held in the normalized form of its scheme.

    :ivar scheme: Kind of the identifier, which decides how it is written and checked.
    :ivar value: The identifier in the normalized form of its scheme, so equal numbers written differently are equal.
    """

    scheme: IdentifierScheme
    value: str = field(validator=_is_normalized)

    @classmethod
    def parse(cls, scheme: IdentifierScheme, raw: str) -> Self:
        """Build an identifier from a value as a person wrote it or a file stored it.

        :param scheme: Kind of the identifier.
        :type scheme: IdentifierScheme
        :param raw: The value in any spelling the scheme accepts.
        :type raw: str
        :returns: The identifier with the value normalized.
        :rtype: Self
        :raises InvalidIdentifierError: If the value breaks the rules of the scheme.
        """
        return cls(scheme=scheme, value=scheme.normalize(raw))


@frozen(kw_only=True)
class ArchivedBook:
    """The archive of a book on its way to the reader: the title it is named after and the stream of its pieces.

    :ivar title: Title of the book, which names the file of the archive.
    :ivar pieces: The pieces of the archive, in order, which the stream reads once.
    """

    title: str
    pieces: AsyncIterator[bytes]


@frozen(kw_only=True)
class BookDetails:
    """Bibliographic description of a printed book, down to the copy that was scanned.

    The fields follow the `DCMI Metadata Terms <https://www.dublincore.org/specifications/dublin-core/dcmi-terms/>`_,
    so an export to library formats needs no translation of concepts. Text fields are empty when unknown, lists are
    tuples, and the title is the one field that may not be empty.

    :ivar title: Title of the book.
    :ivar subtitle: Words of the title page that explain the title.
    :ivar parallel_titles: Titles in other languages that the title page prints beside the title.
    :ivar original_title: Title of the original work, for a translation.
    :ivar contributors: People who made the book, each with a role, in the order of the title page.
    :ivar publisher: Publisher of the book.
    :ivar printer: Printing house, which old books name apart from the publisher.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be a range or an estimate.
    :ivar edition: Edition statement.
    :ivar censorship: Censor's permit printed in the book, whose date dates a book that names no year.
    :ivar series: Series the book belongs to.
    :ivar series_number: Number of the book in its series.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar languages: ISO 639-3 codes of the languages of the text.
    :ivar orthography: Spelling system of the print, such as pre-reform Russian.
    :ivar script: Writing system of the print.
    :ivar printed_pagination: Pagination as a catalogue states it, such as ``XII, 340 p., 8 l. of plates``.
    :ivar height_cm: Height of the book in centimetres, or None when unknown.
    :ivar illustrations: Illustrations as a catalogue states them.
    :ivar binding: Binding or cover of the copy.
    :ivar identifiers: Numbers and addresses that identify the book or the copy, such as a shelfmark.
    :ivar subjects: Topics of the book.
    :ivar rights: Whether the result of the work may be published.
    :ivar copy_holder: Owner of the copy that was scanned.
    :ivar copy_notes: Marks of the copy that was scanned, such as bookplates and annotations.
    :ivar notes: Free-form notes of the owner.
    """

    title: str = field(validator=validators.min_len(1))
    subtitle: str = ''
    parallel_titles: tuple[str, ...] = ()
    original_title: str = ''
    contributors: tuple[Contributor, ...] = ()
    publisher: str = ''
    printer: str = ''
    publication_place: str = ''
    publication_year: str = ''
    edition: str = ''
    censorship: str = ''
    series: str = ''
    series_number: str = ''
    volume: str = ''
    languages: tuple[str, ...] = ()
    orthography: Orthography = Orthography.UNKNOWN
    script: Script = Script.UNKNOWN
    printed_pagination: str = ''
    height_cm: int | None = field(default=None, validator=validators.optional(validators.gt(0)))
    illustrations: str = ''
    binding: str = ''
    identifiers: tuple[BookIdentifier, ...] = ()
    subjects: tuple[str, ...] = ()
    rights: RightsStatus = RightsStatus.UNKNOWN
    copy_holder: str = ''
    copy_notes: str = ''
    notes: str = ''

    def fill_from(self, suggestion: MetadataSuggestion) -> BookDetails:
        """Return the description with every empty field the suggestion has a value for filled in.

        A field that holds a value, even a wrong one, is never replaced, and the title is never touched, because it is
        required when the book is created and a file can only guess it.

        :param suggestion: Description fields found in a source.
        :type suggestion: MetadataSuggestion
        :returns: The description with its empty fields filled, or an equal description when nothing was empty.
        :rtype: BookDetails
        """
        found = {
            name: value
            for name in fields_dict(MetadataSuggestion)
            if name != TITLE_FIELD and (value := getattr(suggestion, name)) and not getattr(self, name)
        }
        return evolve(self, **found)

    @property
    def primary_author(self) -> str:
        """The first author, else the first contributor of any role, else an empty string."""
        for person in self.contributors:
            if person.role is ContributorRole.AUTHOR:
                return person.name
        return self.contributors[0].name if self.contributors else ''


@frozen(kw_only=True)
class MetadataSuggestion:
    """Description fields found in the metadata of a source file; an empty string or list means nothing was found.

    Only the fields that file metadata can give are here, with the types they have in ``BookDetails``. A year comes
    only from metadata that states one, never from the date a file was created or changed.

    :ivar title: Title found in the source.
    :ivar contributors: People found in the source, each with the role the metadata gives them.
    :ivar publisher: Publisher found in the source.
    :ivar publication_year: Year of publication found in the source.
    :ivar languages: ISO 639-3 codes of the languages found in the source.
    :ivar identifiers: Valid ISBNs and web addresses found in the source.
    :ivar subjects: Topics found in the source.
    """

    title: str = ''
    contributors: tuple[Contributor, ...] = ()
    publisher: str = ''
    publication_year: str = ''
    languages: tuple[str, ...] = ()
    identifiers: tuple[BookIdentifier, ...] = ()
    subjects: tuple[str, ...] = ()


@frozen(kw_only=True)
class BookRecord:
    """One book being digitised: its description and the settings of the work on it.

    The record knows nothing of accounts or of the identifier of its book, so a book moves between instances of the
    application as it is. The identifier and the account that owns the book are a :class:`LibraryEntry` of the
    library. The files the book was assembled from are its sources, which the record does not name.

    :ivar details: Bibliographic description of the book.
    :ivar image_policy: How the ``full`` images of the book's scans and page versions are stored.
    :ivar created_at: When the book was created.
    :ivar updated_at: When the book was last changed.
    """

    DEFAULT_IMAGE_POLICY: ClassVar[ImagePolicy] = ImagePolicy.COMPACT

    details: BookDetails
    image_policy: ImagePolicy = DEFAULT_IMAGE_POLICY
    created_at: datetime
    updated_at: datetime
