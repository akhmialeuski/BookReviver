"""Use cases of the place the owner left a book at, which a book is opened at again on any device.

A place belongs to the book, which has one owner, and the book of another account answers like a missing one. The
reader's client writes the place after every move, so the write is one replacement of the whole record and nothing is
merged.
"""

from typing import TYPE_CHECKING

from attrs import asdict

from bookreviver.domain.entities import BookPlace

if TYPE_CHECKING:
    from bookreviver.domain.values import NewBookPlace
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.book import Book


class PlaceService:
    """Reads and replaces the place the owner left a book at."""

    def __init__(self, *, uow: UnitOfWork, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose ``change_book`` ends the use case that changes data.
        :type uow: UnitOfWork
        :param clock: Clock stamping the places.
        :type clock: Clock
        """
        self._uow = uow
        self._clock = clock

    async def find(self, book: Book) -> BookPlace | None:
        """Return the place the owner left the book at.

        :param book: The open book.
        :type book: Book
        :returns: The place, or None when the owner has not worked on the book yet.
        :rtype: BookPlace | None
        """
        return await self._uow.read_book(book.id, lambda repos: repos.book_places.find())

    async def save(self, book: Book, place: NewBookPlace) -> BookPlace:
        """Replace the place the owner left the book at.

        The request is stamped when it starts, and the store keeps the place of the later stamp whole, so two requests
        writing together never leave a mixture of the two and a late one never overwrites a newer one.

        :param book: The open book.
        :type book: Book
        :param place: Where the owner left the book.
        :type place: NewBookPlace
        :returns: The place as stored, which is a newer place of another request when that one was stamped later.
        :rtype: BookPlace
        """
        stored = BookPlace(updated_at=self._clock.now(), **asdict(place, recurse=False))
        return await self._uow.change_book(book.id, lambda repos, _book_record: repos.book_places.save(stored))
