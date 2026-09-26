"""Unit of work over one SQLAlchemy session, which is one database transaction."""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyJobRepository,
    SqlAlchemyPageRepository,
    SqlAlchemyProjectRepository,
)
from bookreviver.ports.persistence import UnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Repositories sharing the session of one request or job, committed or rolled back by the session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.projects = SqlAlchemyProjectRepository(session)
        self.pages = SqlAlchemyPageRepository(session)
        self.jobs = SqlAlchemyJobRepository(session)

    @override
    async def commit(self) -> None:
        await self._session.commit()

    @override
    async def rollback(self) -> None:
        await self._session.rollback()
