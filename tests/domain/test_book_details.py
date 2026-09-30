"""Tests for the description of a book and the people it names."""

import attrs
import pytest

from bookreviver.domain.enums import ContributorRole, IdentifierScheme, Orthography, RightsStatus, Script
from bookreviver.domain.values import BookDetails, BookIdentifier, Contributor, MetadataSuggestion
from tests.helpers.builders import FULL_DETAILS

TITLE: str = 'Сборникъ народныхъ пѣсенъ'
AUTHOR: Contributor = Contributor(name='И. И. Ивановъ', role=ContributorRole.AUTHOR)
EDITOR: Contributor = Contributor(name='П. П. Петровъ', role=ContributorRole.EDITOR)
ENGRAVER: Contributor = Contributor(name='Ф. Ф. Фёдоровъ', role=ContributorRole.ENGRAVER)
# The description fields that hold text, and those that hold a list, which are all empty in a new description
TEXT_FIELDS: list[str] = [
    *('subtitle', 'original_title', 'publisher', 'printer', 'publication_place', 'publication_year', 'edition'),
    *('censorship', 'series', 'series_number', 'volume', 'printed_pagination', 'illustrations', 'binding'),
    *('copy_holder', 'copy_notes', 'notes'),
]
LIST_FIELDS: list[str] = ['parallel_titles', 'contributors', 'languages', 'identifiers', 'subjects']


class TestBookDetails:
    """Tests for BookDetails."""

    def test_only_the_title_is_required_and_everything_else_is_empty(self) -> None:
        """Verify a description of a title alone has empty text, empty lists, no height and unknown enums."""
        details = BookDetails(title=TITLE)

        empty = {
            **dict.fromkeys(TEXT_FIELDS, ''),
            **dict.fromkeys(LIST_FIELDS, ()),
            'height_cm': None,
            'orthography': Orthography.UNKNOWN,
            'script': Script.UNKNOWN,
            'rights': RightsStatus.UNKNOWN,
        }
        assert {name: getattr(details, name) for name in attrs.fields_dict(BookDetails) if name != 'title'} == empty

    @pytest.mark.parametrize('height', [0, -1])
    def test_height_that_is_not_positive_is_rejected(self, height: int) -> None:
        """Verify a book cannot be zero or negative centimetres high.

        :param height: Height in centimetres that is not positive.
        :type height: int
        """
        with pytest.raises(ValueError, match='height_cm'):
            BookDetails(title=TITLE, height_cm=height)

    def test_unknown_height_is_allowed(self) -> None:
        """Verify None stands for a height nobody measured."""
        assert BookDetails(title=TITLE, height_cm=None).height_cm is None


class TestContributor:
    """Tests for Contributor."""

    def test_empty_name_is_rejected(self) -> None:
        """Verify a contributor without a name cannot exist."""
        with pytest.raises(ValueError, match='name'):
            Contributor(name='', role=ContributorRole.AUTHOR)

    def test_name_is_kept_as_printed(self) -> None:
        """Verify the pre-reform spelling and the initials are kept, since a catalogue is searched by them."""
        assert AUTHOR.name == 'И. И. Ивановъ'


class TestPrimaryAuthor:
    """Tests for BookDetails.primary_author."""

    @pytest.mark.parametrize(
        ('contributors', 'expected'),
        [
            ((), ''),
            ((AUTHOR,), AUTHOR.name),
            ((EDITOR, AUTHOR), AUTHOR.name),
            ((EDITOR, ENGRAVER), EDITOR.name),
            ((ENGRAVER, EDITOR, AUTHOR, Contributor(name='Second', role=ContributorRole.AUTHOR)), AUTHOR.name),
        ],
        ids=['none', 'only-author', 'author-after-editor', 'no-author-first-contributor', 'first-of-two-authors'],
    )
    def test_is_the_first_author_else_the_first_contributor(
        self, contributors: tuple[Contributor, ...], expected: str
    ) -> None:
        """Verify the first author is named, else the first contributor of any role, else an empty string.

        :param contributors: Contributors of the description, in title page order.
        :type contributors: tuple[Contributor, ...]
        :param expected: Name the project list must show.
        :type expected: str
        """
        assert BookDetails(title=TITLE, contributors=contributors).primary_author == expected


class TestFillFrom:
    """Tests for BookDetails.fill_from()."""

    def test_fills_every_empty_field_the_suggestion_has_a_value_for(self) -> None:
        """Verify text fields and lists that are empty take the values a source suggests."""
        suggestion = MetadataSuggestion(
            title='Other title',
            contributors=(AUTHOR,),
            publisher='Synodal press',
            publication_year='1902',
            languages=('rus',),
            identifiers=(BookIdentifier.parse(IdentifierScheme.ISBN, '0-306-40615-2'),),
            subjects=('Folklore',),
        )

        filled = BookDetails(title=TITLE).fill_from(suggestion)

        assert filled == BookDetails(
            title=TITLE,
            contributors=suggestion.contributors,
            publisher='Synodal press',
            publication_year='1902',
            languages=('rus',),
            identifiers=suggestion.identifiers,
            subjects=('Folklore',),
        )

    def test_never_replaces_a_field_that_holds_a_value(self) -> None:
        """Verify what the owner entered stays, for a text field and for a list, and only empty fields are filled."""
        details = BookDetails(title=TITLE, publisher='Owner press', contributors=(EDITOR,))
        suggestion = MetadataSuggestion(
            contributors=(AUTHOR,), publisher='Synodal press', publication_year='1902', languages=('rus',)
        )

        filled = details.fill_from(suggestion)

        assert (filled.publisher, filled.contributors, filled.publication_year, filled.languages) == (
            'Owner press',
            (EDITOR,),
            '1902',
            ('rus',),
        )

    def test_never_touches_the_title(self) -> None:
        """Verify a title the file suggests does not replace the one the project was created with."""
        assert BookDetails(title=TITLE).fill_from(MetadataSuggestion(title='From the file')).title == TITLE

    def test_an_empty_suggestion_changes_nothing(self) -> None:
        """Verify a suggestion that found nothing returns an equal description."""
        assert FULL_DETAILS.fill_from(MetadataSuggestion()) == FULL_DETAILS

    def test_leaves_the_fields_a_file_cannot_suggest_alone(self) -> None:
        """Verify fields such as the printer and the notes are never part of what a suggestion fills."""
        suggestion = MetadataSuggestion(publisher='Synodal press')

        filled = BookDetails(title=TITLE, notes='Kept').fill_from(suggestion)

        assert (filled.notes, filled.printer, filled.censorship) == ('Kept', '', '')
