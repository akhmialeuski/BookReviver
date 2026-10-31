"""Tests for partial changes of value objects."""

from typing import TYPE_CHECKING

import attrs
import pytest

from bookreviver.domain.books import (
    BookDetails,
    BookIdentifier,
    Contributor,
    ContributorRole,
    IdentifierScheme,
    Orthography,
    Script,
)
from bookreviver.domain.changes import KEEP, BookChanges, BookDetailsChanges, PageChanges
from bookreviver.domain.images import ImagePolicy
from bookreviver.domain.pages import BlankFill, PageKind
from tests.helpers.builders import FULL_DETAILS, make_page, make_record, make_scan, make_source

if TYPE_CHECKING:
    from bookreviver.domain.pages import Page

NEW_TITLE: str = 'New title'
CURRENT: BookDetails = BookDetails(
    title='Old title',
    contributors=(Contributor(name='A. Author', role=ContributorRole.AUTHOR),),
    notes='Kept',
)
BOOK = attrs.evolve(make_record(), details=CURRENT)


class TestBookDetailsChanges:
    """Tests for BookDetailsChanges."""

    def test_covers_every_description_field(self) -> None:
        """Verify every field of a description can be changed, so the two classes never drift apart."""
        assert attrs.fields_dict(BookDetailsChanges).keys() == attrs.fields_dict(BookDetails).keys()

    def test_keeps_every_field_by_default(self) -> None:
        """Verify a change built without arguments names no field, so it changes nothing."""
        assert all(value is KEEP for value in attrs.asdict(BookDetailsChanges(), recurse=False).values())


class TestApplyTo:
    """Tests for BookDetailsChanges.apply_to()."""

    def test_replaces_given_fields_and_keeps_the_rest(self) -> None:
        """Verify only the fields that were set change, including a field cleared to an empty tuple."""
        changes = BookDetailsChanges(title=NEW_TITLE, contributors=(), orthography=Orthography.PRE_REFORM)

        assert changes.apply_to(CURRENT) == attrs.evolve(
            CURRENT, title=NEW_TITLE, contributors=(), orthography=Orthography.PRE_REFORM
        )

    def test_empty_change_keeps_the_description(self) -> None:
        """Verify a change that sets no field returns the description as it was."""
        assert BookDetailsChanges().apply_to(CURRENT) == CURRENT

    def test_none_clears_the_height_instead_of_keeping_it(self) -> None:
        """Verify None is a value like any other for the height, whose empty value it is, and only KEEP keeps."""
        assert BookDetailsChanges(height_cm=None).apply_to(FULL_DETAILS).height_cm is None

    def test_empty_values_clear_every_kind_of_field(self) -> None:
        """Verify the empty value of a string, a list and an enum each replace the stored value."""
        changes = BookDetailsChanges(subtitle='', identifiers=(), languages=(), script=Script.UNKNOWN, notes='')

        cleared = changes.apply_to(FULL_DETAILS)

        assert (cleared.subtitle, cleared.identifiers, cleared.languages, cleared.script, cleared.notes) == (
            '',
            (),
            (),
            Script.UNKNOWN,
            '',
        )

    def test_a_list_is_replaced_as_a_whole(self) -> None:
        """Verify a new list of identifiers does not merge with the stored ones."""
        isbn = BookIdentifier.parse(IdentifierScheme.ISBN, '0-306-40615-2')

        assert BookDetailsChanges(identifiers=(isbn,)).apply_to(FULL_DETAILS).identifiers == (isbn,)

    def test_empty_title_is_rejected_like_a_new_description(self) -> None:
        """Verify the result is validated, so a change cannot produce an invalid description."""
        with pytest.raises(ValueError, match='title'):
            BookDetailsChanges(title='').apply_to(CURRENT)

    def test_a_height_that_is_not_positive_is_rejected(self) -> None:
        """Verify the result is validated for the height too."""
        with pytest.raises(ValueError, match='height_cm'):
            BookDetailsChanges(height_cm=0).apply_to(CURRENT)


class TestBookChangesApplyTo:
    """Tests for BookChanges.apply_to()."""

    def test_replaces_given_fields_and_keeps_the_rest(self) -> None:
        """Verify the description and the image policy change together, and nothing else does."""
        changes = BookChanges(details=BookDetailsChanges(title=NEW_TITLE), image_policy=ImagePolicy.LOSSLESS)

        assert changes.apply_to(BOOK) == attrs.evolve(
            BOOK, details=attrs.evolve(CURRENT, title=NEW_TITLE), image_policy=ImagePolicy.LOSSLESS
        )

    def test_empty_change_keeps_the_book(self) -> None:
        """Verify a change that sets no field returns the book as it was."""
        assert BookChanges().apply_to(BOOK) == BOOK

    def test_empty_title_is_rejected_like_a_new_description(self) -> None:
        """Verify the result is validated, so a change cannot produce a book with an invalid description."""
        with pytest.raises(ValueError, match='title'):
            BookChanges(details=BookDetailsChanges(title='')).apply_to(BOOK)


class TestPageChangesApplyTo:
    """Tests for PageChanges.apply_to() where the kind of a page carries a leaf in place of its scan."""

    @staticmethod
    def _leaf_page() -> Page:
        """Build a blank page cut from a scan that shows a white leaf.

        :returns: The page.
        :rtype: Page
        """
        scan = make_scan(source=make_source(), number=0)
        return attrs.evolve(make_page(scan=scan, kind=PageKind.BLANK), blank_fill=BlankFill.WHITE)

    def test_a_page_that_stops_being_blank_gets_its_scan_back(self) -> None:
        """Verify the leaf is dropped with the kind, since a leaf stands in place of the scan of a blank page only."""
        changed = PageChanges(kind=PageKind.TEXT).apply_to(self._leaf_page())

        assert (changed.kind, changed.blank_fill) == (PageKind.TEXT, BlankFill.SCAN)

    def test_a_page_that_stays_blank_keeps_its_leaf(self) -> None:
        """Verify a change that leaves the kind alone, or sets the same one, keeps the leaf."""
        leaf = self._leaf_page()

        assert PageChanges(notes='x').apply_to(leaf).blank_fill is BlankFill.WHITE
        assert PageChanges(kind=PageKind.BLANK).apply_to(leaf).blank_fill is BlankFill.WHITE
