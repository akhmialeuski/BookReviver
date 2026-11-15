"""The repository of the place the reader left a book at, the one row of the ``book_places`` table of the book.

The place names no book, so its one row is keyed by a constant, as the record of the book is.
"""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.mappers import BookPlaceMapper
from bookreviver.adapters.persistence.sqlalchemy.rows import BookPlaceRows
from bookreviver.adapters.persistence.sqlalchemy.tables import BOOK_PLACE_ID
from bookreviver.ports.persistence import BookPlaceRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from bookreviver.domain.places import BookPlace


class SqlAlchemyBookPlaceRepository(BookPlaceRepository):
    """The one place the reader left the book at, the one row of ``book_places``.

    :ivar _places: The rows of the table, with the upsert the place is written by.
    :ivar _mapper: Mapper between the place and its row.
    """

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``book_places`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        self._places = BookPlaceRows(session=session)
        self._mapper = BookPlaceMapper()

    @override
    def save(self, place: BookPlace) -> BookPlace:
        """Store the place whole, replacing the stored one unless that one was written later.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored, which is the stored place when it was written later than ``place``.
        :rtype: BookPlace
        """
        return self._mapper.to_entity(self._places.replace_unless_newer(self._mapper.to_row(place)))

    @override
    def find(self) -> BookPlace | None:
        """Return the place of the book.

        :returns: The place, or None when the book has not been worked on yet.
        :rtype: BookPlace | None
        """
        row = self._places.get_one_or_none(id=BOOK_PLACE_ID)
        return None if row is None else self._mapper.to_entity(row)
