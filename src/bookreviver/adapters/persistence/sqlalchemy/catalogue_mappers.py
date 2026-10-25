"""Mappers between the rows of the catalogue and its frozen domain entities: the library.

They follow the Data Mapper pattern of :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers`, whose mappers
translate the tables of a book, and share its base class.
"""

from typing import override

from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
from bookreviver.domain.entities import LibraryEntry
from bookreviver.domain.ids import AccountId, BookDirectoryName, BookId


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
