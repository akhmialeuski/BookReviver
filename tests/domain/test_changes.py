"""Tests for partial changes of value objects."""

from uuid import uuid4

import attrs
import pytest

from bookreviver.domain.changes import BookDetailsChanges, CoverChange, ProjectChanges
from bookreviver.domain.enums import ImagePolicy, Orthography
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import BookDetails
from tests.helpers.builders import make_project, new_account_id

NEW_TITLE: str = 'New title'
CURRENT: BookDetails = BookDetails(title='Old title', authors='A. Author', notes='Kept')
PROJECT = attrs.evolve(make_project(owner_id=new_account_id()), details=CURRENT)


class TestBookDetailsChanges:
    """Tests for BookDetailsChanges."""

    def test_covers_every_description_field(self) -> None:
        """Verify every field of a description can be changed, so the two classes never drift apart."""
        assert attrs.fields_dict(BookDetailsChanges).keys() == attrs.fields_dict(BookDetails).keys()


class TestApplyTo:
    """Tests for BookDetailsChanges.apply_to()."""

    def test_replaces_given_fields_and_keeps_the_rest(self) -> None:
        """Verify only the fields that were set change, including a field cleared to an empty string."""
        changes = BookDetailsChanges(title=NEW_TITLE, authors='', orthography=Orthography.PRE_REFORM)

        assert changes.apply_to(CURRENT) == attrs.evolve(
            CURRENT, title=NEW_TITLE, authors='', orthography=Orthography.PRE_REFORM
        )

    def test_empty_change_keeps_the_description(self) -> None:
        """Verify a change that sets no field returns the description as it was."""
        assert BookDetailsChanges().apply_to(CURRENT) == CURRENT

    def test_empty_title_is_rejected_like_a_new_description(self) -> None:
        """Verify the result is validated, so a change cannot produce an invalid description."""
        with pytest.raises(ValueError, match='title'):
            BookDetailsChanges(title='').apply_to(CURRENT)


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
            details=BookDetails(title=NEW_TITLE, authors='A. Author', notes='Kept'),
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
