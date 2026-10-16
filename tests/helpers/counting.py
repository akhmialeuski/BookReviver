"""A unit of work that counts its reads, for the tests that prove how many snapshots of the database a request takes."""

from typing import TYPE_CHECKING, override

from bookreviver.ports.persistence import UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import Repositories


class CountingUnitOfWork(UnitOfWork):
    """Hands every call to another unit of work, and counts the calls of ``read``.

    :ivar reads: How many times ``read`` was called, each of which is a thread, a connection and a snapshot of the
                 database in an adapter that has them.
    """

    def __init__(self, inner: UnitOfWork) -> None:
        """Wrap a unit of work.

        :param inner: The unit of work that runs the work.
        :type inner: UnitOfWork
        """
        self._inner = inner
        self.reads = 0

    @override
    async def read[T](self, work: Callable[[Repositories], T]) -> T:
        """Count the read and run it on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Repositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        self.reads += 1
        return await self._inner.read(work)

    @override
    async def change[T](self, work: Callable[[Repositories], T]) -> T:
        """Run the change on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Repositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change(work)

    @override
    async def change_book[T](self, project_id: ProjectId, work: Callable[[Repositories, Project], T]) -> T:
        """Run the change of the book on the wrapped unit of work.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project read at its start.
        :type work: Callable[[Repositories, Project], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change_book(project_id, work)
