"""A unit of work that counts its reads, for the tests that prove how many snapshots of a book a request takes."""

from typing import TYPE_CHECKING, override

from bookreviver.ports.persistence import UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories


class CountingUnitOfWork(UnitOfWork):
    """Hands every call to another unit of work, and counts the reads of the catalogue and of the books.

    :ivar reads: How many times ``read_book`` was called, each of which is a thread, a connection and a snapshot of a
                 book in an adapter that has them.
    :ivar catalogue_reads: How many times ``read`` was called, each of which is a snapshot of the catalogue.
    """

    def __init__(self, inner: UnitOfWork) -> None:
        """Wrap a unit of work.

        :param inner: The unit of work that runs the work.
        :type inner: UnitOfWork
        """
        self._inner = inner
        self.reads = 0
        self.catalogue_reads = 0

    @override
    async def read[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Count the read of the catalogue and run it on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        self.catalogue_reads += 1
        return await self._inner.read(work)

    @override
    async def change[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run the change of the catalogue on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change(work)

    @override
    async def read_book[T](self, project_id: ProjectId, work: Callable[[BookRepositories], T]) -> T:
        """Count the read of the book and run it on the wrapped unit of work.

        :param project_id: Project whose book is read.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        self.reads += 1
        return await self._inner.read_book(project_id, work)

    @override
    async def change_book[T](self, project_id: ProjectId, work: Callable[[BookRepositories, Project], T]) -> T:
        """Run the change of the book on the wrapped unit of work.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project read at its start.
        :type work: Callable[[BookRepositories, Project], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change_book(project_id, work)

    @override
    async def create_book(self, project: Project) -> Project:
        """Make the store of a new book on the wrapped unit of work.

        :param project: The project of the new book.
        :type project: Project
        :returns: The project as stored.
        :rtype: Project
        """
        return await self._inner.create_book(project)

    @override
    async def delete_book(self, project_id: ProjectId) -> None:
        """Remove the store of a book on the wrapped unit of work.

        :param project_id: Project whose book is removed.
        :type project_id: ProjectId
        """
        await self._inner.delete_book(project_id)

    @override
    async def stored_books(self) -> Collection[ProjectId]:
        """Return the books the wrapped unit of work stores.

        :returns: The identifiers of the stored books.
        :rtype: Collection[ProjectId]
        """
        return await self._inner.stored_books()
