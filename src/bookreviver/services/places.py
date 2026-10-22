"""Use cases of the place the owner left a book at, which a book is opened at again on any device.

A place belongs to the book, which has one owner, and the book of another account answers like a missing one. The
reader's client writes the place after every move, so the write is one replacement of the whole record and nothing is
merged.
"""

from typing import TYPE_CHECKING

from attrs import asdict

from bookreviver.domain.entities import BookPlace

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import NewBookPlace
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.library import Library


class PlaceService:
    """Reads and replaces the place of the acting account in its books."""

    def __init__(self, *, uow: UnitOfWork, library: Library, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose ``change_book`` ends the use case that changes data.
        :type uow: UnitOfWork
        :param library: Library that opens the books of the acting account.
        :type library: Library
        :param clock: Clock stamping the places.
        :type clock: Clock
        """
        self._uow = uow
        self._library = library
        self._clock = clock

    async def find(self, actor: Actor, project_id: ProjectId) -> BookPlace | None:
        """Return the place the actor left one of their books at.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the book.
        :type project_id: ProjectId
        :returns: The place, or None when the actor has not worked on the book yet.
        :rtype: BookPlace | None
        :raises NotFoundError: If the actor has no such book.
        """
        await self._library.open(actor, project_id)
        return await self._uow.read_book(project_id, lambda repos: repos.book_places.find())

    async def save(self, actor: Actor, project_id: ProjectId, place: NewBookPlace) -> BookPlace:
        """Replace the place the actor left one of their books at.

        The request is stamped when it starts, and the store keeps the place of the later stamp whole, so two requests
        writing together never leave a mixture of the two and a late one never overwrites a newer one.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the book.
        :type project_id: ProjectId
        :param place: Where the actor left the book.
        :type place: NewBookPlace
        :returns: The place as stored, which is a newer place of another request when that one was stamped later.
        :rtype: BookPlace
        :raises NotFoundError: If the actor has no such book.
        """
        await self._library.open(actor, project_id)
        stored = BookPlace(updated_at=self._clock.now(), **asdict(place, recurse=False))
        return await self._uow.change_book(project_id, lambda repos, _project: repos.book_places.save(stored))
