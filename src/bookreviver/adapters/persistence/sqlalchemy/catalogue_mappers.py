"""Mappers between the rows of the catalogue and its frozen domain entities: the library and the recipe profiles.

They follow the Data Mapper pattern of :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers`, whose mappers
translate the tables of a book, and share its base class.
"""

from typing import override

from bookreviver.adapters.persistence.sqlalchemy.catalogue import AccountProfileRow, LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
from bookreviver.domain.entities import AccountProfile, LibraryEntry
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


class AccountProfileMapper(RowMapper[AccountProfile, AccountProfileRow]):
    """Translation of the recipe profile of an account, whose steps are a JSON list."""

    @override
    def to_entity(self, row: AccountProfileRow) -> AccountProfile:
        """Build the profile stored in ``row``.

        :param row: Profile row loaded from the database.
        :type row: AccountProfileRow
        :returns: The profile with its steps.
        :rtype: AccountProfile
        """
        return AccountProfile(
            id=RecipeProfileId(row.id),
            account_id=AccountId(row.account_id),
            stage=row.stage,
            name=row.name,
            steps=tuple(Step.from_map(step) for step in row.steps),
            order=row.order,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: AccountProfile) -> AccountProfileRow:
        """Build the row of ``entity``.

        :param entity: Profile to store.
        :type entity: AccountProfile
        :returns: Transient profile row.
        :rtype: AccountProfileRow
        """
        return AccountProfileRow(
            id=entity.id,
            account_id=entity.account_id,
            stage=entity.stage,
            name=entity.name,
            steps=[step.to_map() for step in entity.steps],
            order=entity.order,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
