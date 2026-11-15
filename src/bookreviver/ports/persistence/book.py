"""The repository ports of the record of a book and of the place the reader left it at, one of each per book."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.library import BookOverview
    from bookreviver.domain.places import BookPlace


class BookRecordRepository(ABC):
    """The record of the book of the store, the one record the store has, which names no identifier of the book."""

    @abstractmethod
    def get(self) -> BookRecord:
        """Return the record of the book.

        :returns: The stored record.
        :rtype: BookRecord
        :raises NotFoundError: If the store holds no record yet.
        """

    @abstractmethod
    def add(self, book_record: BookRecord) -> BookRecord:
        """Store the record of a book whose store holds none yet, and return it as stored.

        :param book_record: The record to store.
        :type book_record: BookRecord
        :returns: The record as stored.
        :rtype: BookRecord
        :raises ConflictError: If the store holds a record already.
        """

    @abstractmethod
    def update(self, book_record: BookRecord) -> BookRecord:
        """Replace the stored record of the book and return it as stored.

        :param book_record: The record with its new state.
        :type book_record: BookRecord
        :returns: The record as stored.
        :rtype: BookRecord
        :raises NotFoundError: If the store holds no record, or the new cover is not a page of the book.
        """

    @abstractmethod
    def overview(self) -> BookOverview:
        """Return the record of the book together with the counts of the book.

        :returns: The record of the book with the counts of its included pages, sources and scans.
        :rtype: BookOverview
        :raises NotFoundError: If the store holds no record yet.
        """


class BookPlaceRepository(ABC):
    """The one place the reader left the book at, which goes with the file of the book."""

    @abstractmethod
    def save(self, place: BookPlace) -> BookPlace:
        """Store the place of the book, replacing the one stored whole, unless that one is newer.

        The write is atomic: of two transactions saving at once, the one with the later ``updated_at`` leaves its place
        whole, and no field of the other is kept. A place whose ``updated_at`` is earlier than the stored one changes
        nothing, so a request that arrives late never overwrites a newer one. The comparison sees the place as last
        committed by anyone, not as this transaction read it.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored, which is the stored place when it is newer than ``place``.
        :rtype: BookPlace
        """

    @abstractmethod
    def find(self) -> BookPlace | None:
        """Return the place of the book.

        :returns: The place, or None when the book has not been worked on yet.
        :rtype: BookPlace | None
        """
