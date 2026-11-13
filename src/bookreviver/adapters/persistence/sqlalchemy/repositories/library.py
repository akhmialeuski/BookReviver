"""The library repository of the catalogue, and the repositories of a transaction of the catalogue."""

from typing import TYPE_CHECKING, override

from advanced_alchemy.filters import LimitOffset

from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.catalogue_mappers import LibraryEntryMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyRepository
from bookreviver.adapters.persistence.sqlalchemy.rows import LibraryRows
from bookreviver.domain.ids import BookId
from bookreviver.domain.library import LibraryEntry
from bookreviver.domain.slices import Slice
from bookreviver.ports.persistence import (
    CatalogueRepositories,
    LibraryRepository,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.slices import SliceRequest


class SqlAlchemyLibraryRepository(SqlAlchemyRepository[LibraryEntry, BookId, LibraryRow], LibraryRepository):
    """The library of the catalogue, one entry for each book, listed per owner."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``library`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=LibraryRows(session=session), mapper=LibraryEntryMapper())

    @override
    def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[LibraryEntry]:
        """Return a slice of the owner's books, the latest added first, ties by identifier.

        The total is a separate count, which stays correct for a slice past the end where a window-function count
        would report zero.

        :param owner_id: Account whose books are listed.
        :type owner_id: AccountId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Entries of the slice, and the total number of the owner's books.
        :rtype: Slice[LibraryEntry]
        """
        rows = self._rows.get_many(
            LimitOffset(limit=request.limit, offset=request.offset),
            order_by=[LibraryRow.added_at.desc(), LibraryRow.book_id.asc()],
            owner_id=owner_id,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=self._rows.count(owner_id=owner_id))

    @override
    def list_all(self) -> Sequence[LibraryEntry]:
        """Return the entry of every book, by identifier.

        :returns: Every entry of the library.
        :rtype: Sequence[LibraryEntry]
        """
        return [self._mapper.to_entity(row) for row in self._rows.get_many(order_by=LibraryRow.book_id.asc())]


class SqlAlchemyCatalogueRepositories(CatalogueRepositories):
    """Every repository of the catalogue, bound to the session of one transaction of the catalogue.

    The catalogue database builds one for each transaction and hands it to the work, so the repositories share the
    session, and with it the transaction, and are dropped with it.

    :ivar library: Library repository bound to the session.
    """

    def __init__(self, session: Session) -> None:
        """Bind every repository of the catalogue to ``session``.

        :param session: Session of the transaction, owned and closed by the database that runs the work.
        :type session: Session
        """
        self.library = SqlAlchemyLibraryRepository(session)
