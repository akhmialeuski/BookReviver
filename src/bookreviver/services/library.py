"""The library: which account owns which book, and the one check that an account may work on a book.

A book knows nothing of accounts, so every use case of a book asks the library first, in a read of the catalogue, and
only then reads or changes the book. A book of another account answers like a missing one, so nobody learns from the
answer that the book exists.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.errors import NotFoundError

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, LibraryEntry
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import UnitOfWork


class Library:
    """Opens the books of the acting account, and refuses every other book."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        """Work over the unit of work of one request or job.

        :param uow: Unit of work whose ``read`` of the catalogue finds the entry of a book.
        :type uow: UnitOfWork
        """
        self._uow = uow

    async def open(self, actor: Actor, project_id: ProjectId) -> LibraryEntry:
        """Return the entry of a book of the actor, before the use case reads or changes the book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the book.
        :type project_id: ProjectId
        :returns: The entry of the book, owned by the actor.
        :rtype: LibraryEntry
        :raises NotFoundError: If the library has no such book, or the book belongs to another account.
        """
        entry = await self._uow.read(lambda repos: repos.library.get(project_id))
        if not entry.is_owned_by(actor):
            raise NotFoundError(project_id)
        return entry
