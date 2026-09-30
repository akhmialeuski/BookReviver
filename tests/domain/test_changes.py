"""Tests for partial changes of value objects."""

from uuid import uuid4

import attrs
import pytest

from bookreviver.domain.changes import KEEP, BookDetailsChanges, CoverChange, ProjectChanges
from bookreviver.domain.enums import ContributorRole, IdentifierScheme, ImagePolicy, Orthography, Script
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import BookDetails, BookIdentifier, Contributor
from tests.helpers.builders import FULL_DETAILS, make_project, new_account_id

NEW_TITLE: str = 'New title'
CURRENT: BookDetails = BookDetails(
    title='Old title',
    contributors=(Contributor(name='A. Author', role=ContributorRole.AUTHOR),),
    notes='Kept',
)
PROJECT = attrs.evolve(make_project(owner_id=new_account_id()), details=CURRENT)


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


class TestProjectChangesApplyTo:
    """Tests for ProjectChanges.apply_to()."""

    def test_replaces_given_fields_and_keeps_the_rest(self) -> None:
        """Verify the description, the image policy and the cover change together, and nothing else does."""
        cover = PageId(uuid4())
        changes = ProjectChanges(
            details=BookDetailsChanges(title=NEW_TITLE),
            image_policy=ImagePolicy.LOSSLESS,
            cover=CoverChange(page_id=cover),
        )

        assert changes.apply_to(PROJECT) == attrs.evolve(
            PROJECT,
            details=attrs.evolve(CURRENT, title=NEW_TITLE),
            image_policy=ImagePolicy.LOSSLESS,
            cover_page_id=cover,
        )

    def test_empty_change_keeps_the_project(self) -> None:
        """Verify a change that sets no field returns the project as it was, its cover included."""
        covered = attrs.evolve(PROJECT, cover_page_id=PageId(uuid4()))

        assert ProjectChanges().apply_to(covered) == covered

    def test_empty_cover_change_removes_the_cover(self) -> None:
        """Verify a cover change without a page clears the cover, which a missing change would keep."""
        covered = attrs.evolve(PROJECT, cover_page_id=PageId(uuid4()))

        assert ProjectChanges(cover=CoverChange(page_id=None)).apply_to(covered).cover_page_id is None

    def test_empty_title_is_rejected_like_a_new_description(self) -> None:
        """Verify the result is validated, so a change cannot produce a project with an invalid description."""
        with pytest.raises(ValueError, match='title'):
            ProjectChanges(details=BookDetailsChanges(title='')).apply_to(PROJECT)
