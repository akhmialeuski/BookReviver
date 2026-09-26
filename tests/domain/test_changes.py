"""Tests for partial changes of value objects."""

import attrs
import pytest

from bookreviver.domain.changes import BookDetailsChanges
from bookreviver.domain.enums import Orthography
from bookreviver.domain.values import BookDetails

NEW_TITLE: str = 'New title'
CURRENT: BookDetails = BookDetails(title='Old title', authors='A. Author', notes='Kept')


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

    def test_empty_title_is_rejected_like_a_new_description(self) -> None:
        """Verify the result is validated, so a change cannot produce an invalid description."""
        with pytest.raises(ValueError, match='title'):
            BookDetailsChanges(title='').apply_to(CURRENT)
