"""Tests for the description of a book and the people it names."""

import attrs
import pytest

from bookreviver.domain.enums import ContributorRole, Orthography, RightsStatus, Script
from bookreviver.domain.values import BookDetails, Contributor

TITLE: str = 'Сборникъ народныхъ пѣсенъ'
AUTHOR: Contributor = Contributor(name='И. И. Ивановъ', role=ContributorRole.AUTHOR)
EDITOR: Contributor = Contributor(name='П. П. Петровъ', role=ContributorRole.EDITOR)
ENGRAVER: Contributor = Contributor(name='С. С. Сидоровъ', role=ContributorRole.ENGRAVER)


class TestBookDetails:
    """Tests for BookDetails."""

    def test_only_the_title_is_required_and_everything_else_is_empty(self) -> None:
        """Verify a description of a title alone has empty text, empty lists, no height and unknown enums."""
        details = BookDetails(title=TITLE)

        empty = {name for name in attrs.fields_dict(BookDetails) if name != 'title'}
        assert {name: getattr(details, name) for name in empty} == {
            **dict.fromkeys(
                ['subtitle', 'original_title', 'publisher', 'printer', 'publication_place', 'publication_year']
                + ['edition', 'censorship', 'series', 'series_number', 'volume', 'printed_pagination']
                + ['illustrations', 'binding', 'copy_holder', 'copy_notes', 'notes'],
                '',
            ),
            **dict.fromkeys(
                ['parallel_titles', 'contributors', 'languages', 'identifiers', 'subjects'],
                (),
            ),
            'height_cm': None,
            'orthography': Orthography.UNKNOWN,
            'script': Script.UNKNOWN,
            'rights': RightsStatus.UNKNOWN,
        }

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
