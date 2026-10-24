"""Mappers between the rows of the catalogue and its frozen domain entities: the library and the recipe profiles.

They follow the Data Mapper pattern of :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers`, whose mappers
translate the tables of a book, and share its base class.
"""

from typing import override

from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow, RecipeProfileRow
from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
from bookreviver.domain.entities import LibraryEntry, RecipeProfile
from bookreviver.domain.ids import AccountId, BookDirectoryName, BookId, RecipeProfileId
from bookreviver.domain.values import Step


class LibraryEntryMapper(RowMapper[LibraryEntry, LibraryRow]):
    """Translation of an entry of the library, whose columns are its fields."""

    @override
    def to_entity(self, row: LibraryRow) -> LibraryEntry:
        """Build the entry stored in ``row``.

        :param row: Library row loaded from the catalogue.
        :type row: LibraryRow
        :returns: The entry.
        :rtype: LibraryEntry
        """
        return LibraryEntry(
            book_id=BookId(row.book_id),
            owner_id=AccountId(row.owner_id),
            directory=BookDirectoryName(row.directory),
            added_at=row.added_at,
        )

    @override
    def to_row(self, entity: LibraryEntry) -> LibraryRow:
        """Build the row of ``entity``.

        :param entity: Entry to store.
        :type entity: LibraryEntry
        :returns: Transient library row.
        :rtype: LibraryRow
        """
        return LibraryRow(
            book_id=entity.book_id, owner_id=entity.owner_id, directory=entity.directory, added_at=entity.added_at
        )


class RecipeProfileMapper(RowMapper[RecipeProfile, RecipeProfileRow]):
    """Translation of a recipe profile, whose steps are a JSON list."""

    @override
    def to_entity(self, row: RecipeProfileRow) -> RecipeProfile:
        """Build the profile stored in ``row``.

        :param row: Profile row loaded from the database.
        :type row: RecipeProfileRow
        :returns: The profile with its steps.
        :rtype: RecipeProfile
        """
        return RecipeProfile(
            id=RecipeProfileId(row.id),
            account_id=AccountId(row.account_id),
            stage=row.stage,
            name=row.name,
            steps=tuple(Step.from_map(step) for step in row.steps),
            order=row.order,
            is_default=row.is_default,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: RecipeProfile) -> RecipeProfileRow:
        """Build the row of ``entity``.

        :param entity: Profile to store.
        :type entity: RecipeProfile
        :returns: Transient profile row.
        :rtype: RecipeProfileRow
        """
        return RecipeProfileRow(
            id=entity.id,
            account_id=entity.account_id,
            stage=entity.stage,
            name=entity.name,
            steps=[step.to_map() for step in entity.steps],
            order=entity.order,
            is_default=entity.is_default,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
