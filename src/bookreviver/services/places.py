"""Use cases of the place an account left a book at, which a book is opened at again on any device.

A place belongs to the pair of the account and the book, so two accounts reading one book never see each other's, and
the book of another account answers like a missing one. The reader's client writes the place after every move, so the
write is one replacement of the whole record and nothing is merged.
"""

from typing import TYPE_CHECKING

from attrs import asdict

from bookreviver.domain.entities import BookPlace
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.values import BookPlaceKey
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import NewBookPlace
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock


class PlaceService:
    """Reads and replaces the place of the acting account in its books."""

    def __init__(self, *, uow: UnitOfWork, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends the use case that changes data.
        :type uow: UnitOfWork
        :param clock: Clock stamping the places.
        :type clock: Clock
        """
        self._uow = uow
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
        await owned_project(self._uow.projects, actor, project_id)
        return await self._uow.book_places.find(BookPlaceKey(actor.account_id, project_id))

    async def save(self, actor: Actor, project_id: ProjectId, place: NewBookPlace) -> BookPlace:
        """Replace the place the actor left one of their books at.

        Two requests may store the first place of a book together, when the client writes after a pause and again as
        the page closes. The database lets one in and refuses the other, which is stored again as a replacement.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the book.
        :type project_id: ProjectId
        :param place: Where the actor left the book.
        :type place: NewBookPlace
        :returns: The place as stored, with the time of the write.
        :rtype: BookPlace
        :raises NotFoundError: If the actor has no such book.
        """
        await owned_project(self._uow.projects, actor, project_id)
        stored = BookPlace(
            account_id=actor.account_id,
            project_id=project_id,
            updated_at=self._clock.now(),
            **asdict(place, recurse=False),
        )
        try:
            await self._uow.book_places.save(stored)
            await self._uow.commit()
        except ConflictError:
            await self._uow.rollback()
            await self._uow.book_places.save(stored)
            await self._uow.commit()
        return stored
