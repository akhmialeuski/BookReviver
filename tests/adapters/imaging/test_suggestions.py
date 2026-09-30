"""Tests for SuggestionBuilder, which turns the metadata of PDF and DjVu files into a suggested description."""

import pytest

from bookreviver.adapters.imaging.suggestions import SuggestionBuilder
from bookreviver.domain.enums import ContributorRole, IdentifierScheme
from bookreviver.domain.values import BookIdentifier, Contributor, MetadataSuggestion
from tests.adapters.imaging.samples import xmp_packet

TITLE: str = 'Сборникъ народныхъ пѣсенъ'
OTHER_TITLE: str = 'Zbiór pieśni ludowych'
IVANOV: str = 'Ивановъ, Н. Н.'
PETROV: str = 'Петровъ, П. П.'
PUBLISHER: str = 'Изданіе автора'
ISBN: str = '0306406152'
ADDRESS: str = 'https://example.org/books/1'
ENTITY_DOCTYPE: str = (
    '<?xml version="1.0"?><!DOCTYPE xmpmeta [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>'
)
EXTERNAL_DOCTYPE: str = '<?xml version="1.0"?><!DOCTYPE xmpmeta [<!ENTITY secret SYSTEM "file:///etc/passwd">]>'
ENTITY_BODY: str = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>&{name};</dc:title>'
    '</rdf:Description></rdf:RDF></x:xmpmeta>'
)


def _authors(*names: str) -> tuple[Contributor, ...]:
    """Build contributors with the role of author.

    :param names: Names as printed.
    :type names: str
    :returns: One author per name, in order.
    :rtype: tuple[Contributor, ...]
    """
    return tuple(Contributor(name=name, role=ContributorRole.AUTHOR) for name in names)


class TestFromDocinfo:
    """Tests for SuggestionBuilder.from_docinfo()."""

    def test_reads_the_title_and_splits_authors_at_semicolons_only(self) -> None:
        """Verify a comma stays inside a name, since it separates surname and initials in an author string."""
        suggestion = SuggestionBuilder.from_docinfo({'title': f' {TITLE}\n', 'author': f'{IVANOV}; {PETROV};'})

        assert (suggestion.title, suggestion.contributors) == (TITLE, _authors(IVANOV, PETROV))

    def test_takes_the_subject_whole_and_splits_keywords_at_commas_and_semicolons(self) -> None:
        """Verify the subject is one topic, the keywords are many, and a repeat is kept once."""
        info = {'subject': 'Фольклор, песни', 'keywords': 'Фольклор; Народные песни, , Белоруссія'}

        assert SuggestionBuilder.from_docinfo(info).subjects == (
            'Фольклор, песни',
            'Фольклор',
            'Народные песни',
            'Белоруссія',
        )

    def test_reads_missing_and_empty_values_as_nothing(self) -> None:
        """Verify None, an empty string and absent keys give an empty suggestion."""
        assert SuggestionBuilder.from_docinfo({'title': None, 'author': '', 'format': 'PDF 1.7'}) == MetadataSuggestion()

    def test_never_takes_a_year_from_the_dates_of_the_file(self) -> None:
        """Verify the creation and modification dates describe the file and stay out of the year of publication."""
        info = {'title': TITLE, 'creationDate': "D:20240512101500+03'00'", 'modDate': "D:20240601000000Z"}

        assert SuggestionBuilder.from_docinfo(info).publication_year == ''


class TestFromXmp:
    """Tests for SuggestionBuilder.from_xmp()."""

    def test_reads_every_dublin_core_element(self) -> None:
        """Verify the title, creators, contributors, publisher, languages, identifiers and subjects are read."""
        packet = xmp_packet(
            f'<dc:title><rdf:Alt><rdf:li xml:lang="pl">{OTHER_TITLE}</rdf:li>'
            f'<rdf:li xml:lang="x-default">{TITLE}</rdf:li></rdf:Alt></dc:title>'
            f'<dc:creator><rdf:Seq><rdf:li>{IVANOV}</rdf:li><rdf:li>{PETROV}</rdf:li></rdf:Seq></dc:creator>'
            '<dc:contributor><rdf:Bag><rdf:li>Сидоровъ, С. С.</rdf:li></rdf:Bag></dc:contributor>'
            f'<dc:publisher><rdf:Bag><rdf:li>{PUBLISHER}</rdf:li></rdf:Bag></dc:publisher>'
            '<dc:language><rdf:Bag><rdf:li>ru-RU</rdf:li><rdf:li>be</rdf:li><rdf:li>rus</rdf:li></rdf:Bag></dc:language>'
            f'<dc:identifier><rdf:Bag><rdf:li>urn:isbn:0-306-40615-2</rdf:li><rdf:li>{ADDRESS}</rdf:li></rdf:Bag>'
            '</dc:identifier>'
            '<dc:subject><rdf:Bag><rdf:li>Фольклор</rdf:li><rdf:li>Народные песни</rdf:li></rdf:Bag></dc:subject>'
        )

        assert SuggestionBuilder.from_xmp(packet) == MetadataSuggestion(
            title=TITLE,
            contributors=(
                *_authors(IVANOV, PETROV),
                Contributor(name='Сидоровъ, С. С.', role=ContributorRole.CONTRIBUTOR),
            ),
            publisher=PUBLISHER,
            languages=('rus', 'bel'),
            identifiers=(
                BookIdentifier.parse(IdentifierScheme.ISBN, ISBN),
                BookIdentifier.parse(IdentifierScheme.URL, ADDRESS),
            ),
            subjects=('Фольклор', 'Народные песни'),
        )

    def test_reads_an_element_written_as_plain_text(self) -> None:
        """Verify an identifier stored as the text of the element, with no list, is read too."""
        packet = xmp_packet(f'<dc:identifier>ISBN {ISBN}</dc:identifier>')

        assert SuggestionBuilder.from_xmp(packet).identifiers == (BookIdentifier.parse(IdentifierScheme.ISBN, ISBN),)

    @pytest.mark.parametrize(
        'identifier',
        ['0-306-40615-3', 'urn:uuid:6f1c2c6e-1b0c-4b6e-a3a0-2d9b6b0c1f00', 'ftp://example.org/book', 'shelf 18.1'],
    )
    def test_drops_an_identifier_that_is_neither_a_valid_isbn_nor_a_web_address(self, identifier: str) -> None:
        """Verify a wrong check digit, another URN, another scheme and free text give no identifier.

        :param identifier: Value of ``dc:identifier`` that must not become an identifier.
        :type identifier: str
        """
        packet = xmp_packet(f'<dc:identifier>{identifier}</dc:identifier>')

        assert SuggestionBuilder.from_xmp(packet).identifiers == ()

    def test_drops_a_language_tag_that_is_no_iso_639_code(self) -> None:
        """Verify unknown two-letter tags and words give no language."""
        packet = xmp_packet('<dc:language><rdf:Bag><rdf:li>zz</rdf:li><rdf:li>x-default</rdf:li></rdf:Bag></dc:language>')

        assert SuggestionBuilder.from_xmp(packet).languages == ()

    def test_never_takes_a_year_from_dc_date(self) -> None:
        """Verify ``dc:date`` is any date of the life of the file, such as the day it was scanned, and is ignored."""
        packet = xmp_packet(f'<dc:title>{TITLE}</dc:title><dc:date><rdf:Seq><rdf:li>2024-05-12</rdf:li></rdf:Seq></dc:date>')

        suggestion = SuggestionBuilder.from_xmp(packet)

        assert (suggestion.title, suggestion.publication_year) == (TITLE, '')

    @pytest.mark.parametrize(
        ('doctype', 'entity'),
        [(ENTITY_DOCTYPE, 'lol2'), (EXTERNAL_DOCTYPE, 'secret')],
        ids=['nested-entities', 'external-entity'],
    )
    def test_a_packet_declaring_entities_gives_an_empty_suggestion(self, doctype: str, entity: str) -> None:
        """Verify entity expansion and external files are refused, so nothing of the packet is used.

        :param doctype: Document type declaration that declares the entities.
        :type doctype: str
        :param entity: Name of the entity the title refers to.
        :type entity: str
        """
        packet = doctype + ENTITY_BODY.format(name=entity)

        assert SuggestionBuilder.from_xmp(packet) == MetadataSuggestion()

    @pytest.mark.parametrize('packet', ['', '   ', 'not xml at all', '<x:xmpmeta'], ids=['empty', 'blank', 'text', 'cut'])
    def test_a_missing_or_broken_packet_gives_an_empty_suggestion(self, packet: str) -> None:
        """Verify PyMuPDF's empty string for no packet, and text that is not XML, read as nothing found.

        :param packet: What ``get_xml_metadata`` returned.
        :type packet: str
        """
        assert SuggestionBuilder.from_xmp(packet) == MetadataSuggestion()


class TestFromDjvuMeta:
    """Tests for SuggestionBuilder.from_djvu_meta()."""

    def test_reads_the_bibtex_keys(self) -> None:
        """Verify the title, authors, editors, publisher and year are read, and the year is the only date taken."""
        meta = {
            'title': TITLE,
            'author': f'{IVANOV}; {PETROV}',
            'editor': 'Сидоровъ, С. С.',
            'publisher': PUBLISHER,
            'year': ' 1896 ',
        }

        assert SuggestionBuilder.from_djvu_meta(meta) == MetadataSuggestion(
            title=TITLE,
            contributors=(
                *_authors(IVANOV, PETROV),
                Contributor(name='Сидоровъ, С. С.', role=ContributorRole.EDITOR),
            ),
            publisher=PUBLISHER,
            publication_year='1896',
        )

    def test_reads_the_docinfo_keys_when_no_bibtex_key_exists(self) -> None:
        """Verify capitalised keys fill a suggestion the lower-case keys do not cover."""
        suggestion = SuggestionBuilder.from_djvu_meta({'Title': TITLE, 'Author': IVANOV})

        assert (suggestion.title, suggestion.contributors) == (TITLE, _authors(IVANOV))

    def test_bibtex_keys_win_over_docinfo_keys_field_by_field(self) -> None:
        """Verify a BibTeX title beats a DocInfo one, while the DocInfo author fills the field BibTeX lacks."""
        meta = {'Title': OTHER_TITLE, 'title': TITLE, 'Author': IVANOV}

        suggestion = SuggestionBuilder.from_djvu_meta(meta)

        assert (suggestion.title, suggestion.contributors) == (TITLE, _authors(IVANOV))

    def test_ignores_keys_that_describe_no_field(self) -> None:
        """Verify tool names and other keys give an empty suggestion."""
        assert SuggestionBuilder.from_djvu_meta({'Creator': 'Scanner', 'Producer': 'DjVuLibre'}) == MetadataSuggestion()


class TestMerge:
    """Tests for SuggestionBuilder.merge()."""

    def test_takes_the_first_non_empty_value_of_each_field(self) -> None:
        """Verify priority is decided per field, so a lower source fills what a higher one lacks."""
        first = MetadataSuggestion(title=TITLE, subjects=('Фольклор',))
        second = MetadataSuggestion(title=OTHER_TITLE, publisher=PUBLISHER, subjects=('Песни',))

        assert SuggestionBuilder.merge(first, second) == MetadataSuggestion(
            title=TITLE, publisher=PUBLISHER, subjects=('Фольклор',)
        )

    def test_does_not_join_the_lists_of_different_sources(self) -> None:
        """Verify the authors of the higher source are not mixed with those of the lower one."""
        merged = SuggestionBuilder.merge(
            MetadataSuggestion(contributors=_authors(IVANOV)), MetadataSuggestion(contributors=_authors(PETROV))
        )

        assert merged.contributors == _authors(IVANOV)

    def test_merging_nothing_gives_an_empty_suggestion(self) -> None:
        """Verify there is nothing to take from no suggestions."""
        assert SuggestionBuilder.merge() == MetadataSuggestion()
