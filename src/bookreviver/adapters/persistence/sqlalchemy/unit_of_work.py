"""Unit of work over one SQLAlchemy session, which is one database transaction.

The session is opened per HTTP request or background job by the application's ``DatabaseProvider`` and closed by it,
so the unit of work owns no connection. It binds every port repository to that session, which makes every
change they make part of one transaction, visible to others only after its block commits.

A block of the port, ``change_book`` or ``change``, ends the read-only transaction the session may have open, takes a
connection with the execution option that makes SQLite begin with ``BEGIN IMMEDIATE``, and so holds the write lock of
the database from the first line of the block to its commit. ``change_book`` also locks the row of the project with
``FOR NO KEY UPDATE``, which PostgreSQL renders and SQLite leaves out, so on PostgreSQL two changes of one book wait
for each other and changes of different books do not. ``change`` takes no row lock, so on PostgreSQL two of its blocks
may overlap, and PostgreSQL has no wait limit of its own here: the limit is the busy timeout of SQLite.

The session carries one unit of work, which registers its write guard on it. Two units of work over one session would
each guard against the block of the other.
"""

from contextlib import asynccontextmanager
from itertools import chain
from sqlite3 import OperationalError as SqliteOperationalError
from typing import TYPE_CHECKING, override

import anyio
from advanced_alchemy.base import DefaultBase
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from bookreviver.adapters.persistence.sqlalchemy.database import CHANGE_BLOCK_OPTION
from bookreviver.adapters.persistence.sqlalchemy.mappers import ProjectMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyBookPlaceRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyPageRepository,
    SqlAlchemyPageStageRepository,
    SqlAlchemyPageStepChangeRepository,
    SqlAlchemyPageStepStateRepository,
    SqlAlchemyPageVersionRepository,
    SqlAlchemyPaginationSectionRepository,
    SqlAlchemyProjectRepository,
    SqlAlchemyRecipeProfileRepository,
    SqlAlchemyRecipeRepository,
    SqlAlchemyResultMarkChangeRepository,
    SqlAlchemyScanRepository,
    SqlAlchemySourceRepository,
    SqlAlchemyStepValuesRepository,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import ProjectRow
from bookreviver.domain.errors import BookBusyError, NotFoundError
from bookreviver.ports.persistence import NestedChangeError, NoChangeOpenError, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import ORMExecuteState, Session

    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId

# The name SQLite gives the error of a statement that waited the busy timeout and then gave up
SQLITE_BUSY: str = 'SQLITE_BUSY'


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Repositories sharing the session of one request or job, committed or rolled back through the session.

    :ivar projects: Project repository bound to the session.
    :ivar sources: Source repository bound to the session.
    :ivar scans: Scan repository bound to the session.
    :ivar pages: Page repository bound to the session.
    :ivar pagination_sections: Pagination section repository bound to the session.
    :ivar page_versions: Page version repository bound to the session.
    :ivar page_stages: Page stage repository bound to the session.
    :ivar page_step_states: Page step state repository bound to the session.
    :ivar page_step_changes: Page step change repository bound to the session.
    :ivar step_values: Step values repository bound to the session.
    :ivar result_mark_changes: Result mark change repository bound to the session.
    :ivar recipes: Recipe repository bound to the session.
    :ivar recipe_profiles: Recipe profile repository bound to the session.
    :ivar jobs: Job repository bound to the session.
    :ivar book_places: Book place repository bound to the session.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repositories to ``session``.

        :param session: Session of the current request or job, owned and closed by its provider.
        :type session: AsyncSession
        """
        self._session = session
        self.projects = SqlAlchemyProjectRepository(session)
        self.sources = SqlAlchemySourceRepository(session)
        self.scans = SqlAlchemyScanRepository(session)
        self.pages = SqlAlchemyPageRepository(session)
        self.pagination_sections = SqlAlchemyPaginationSectionRepository(session)
        self.page_versions = SqlAlchemyPageVersionRepository(session)
        self.page_stages = SqlAlchemyPageStageRepository(session)
        self.page_step_states = SqlAlchemyPageStepStateRepository(session)
        self.page_step_changes = SqlAlchemyPageStepChangeRepository(session)
        self.step_values = SqlAlchemyStepValuesRepository(session)
        self.result_mark_changes = SqlAlchemyResultMarkChangeRepository(session)
        self.recipes = SqlAlchemyRecipeRepository(session)
        self.recipe_profiles = SqlAlchemyRecipeProfileRepository(session)
        self.jobs = SqlAlchemyJobRepository(session)
        self.book_places = SqlAlchemyBookPlaceRepository(session)
        self._is_open = False
        event.listen(session.sync_session, 'before_flush', self._guard_flush)
        event.listen(session.sync_session, 'do_orm_execute', self._guard_statement)

    @override
    @asynccontextmanager
    async def change_book(self, project_id: ProjectId) -> AsyncIterator[Project]:
        """Open a block that holds the book, by the write lock of SQLite or the row lock of the project.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :returns: Iterator yielding the project as it stands once the lock is held.
        :rtype: AsyncIterator[Project]
        :raises NotFoundError: If the project is not stored.
        :raises BookBusyError: If SQLite's write lock was not free within the busy timeout.
        :raises NestedChangeError: If a block of this unit of work is open already.
        """
        async with self._block():
            locked = select(ProjectRow).where(ProjectRow.id == project_id).with_for_update(key_share=True)
            if (row := await self._session.scalar(locked)) is None:
                raise NotFoundError(project_id)
            yield ProjectMapper().to_entity(row)

    @override
    @asynccontextmanager
    async def change(self) -> AsyncIterator[None]:
        """Open a block that writes outside the content of a book, holding the write lock of SQLite.

        :returns: Iterator yielding once the lock is held.
        :rtype: AsyncIterator[None]
        :raises BookBusyError: If SQLite's write lock was not free within the busy timeout.
        :raises NestedChangeError: If a block of this unit of work is open already.
        """
        async with self._block():
            yield

    @asynccontextmanager
    async def _block(self) -> AsyncIterator[None]:
        """Run a transaction that begins holding the write lock, commits on exit and rolls back on an exception.

        :returns: Iterator yielding once the transaction has begun.
        :rtype: AsyncIterator[None]
        :raises BookBusyError: If SQLite's write lock was not free within the busy timeout.
        :raises NestedChangeError: If a block of this unit of work is open already.
        """
        if self._is_open:
            raise NestedChangeError
        self._is_open = True
        try:
            await self._begin_holding_the_lock()
            yield
            await self._session.commit()
        except BaseException:
            # A cancelled task still has to give the lock back
            with anyio.CancelScope(shield=True):
                await self._session.rollback()
            raise
        finally:
            self._is_open = False

    async def _begin_holding_the_lock(self) -> None:
        """Begin the transaction of a block, which takes the write lock of SQLite.

        The session's read-only transaction is ended first, and every row of the adapter's tables it loaded is expired,
        so the block reads what has been committed since and not what an earlier read or block left in the session.

        The read-only transaction ends by a commit, which sends nothing to SQLite in autocommit mode and, as the
        sessions do not expire on commit, keeps every other object loaded. A rollback, or expiring the whole session,
        would expire the account row that fastapi-users loaded for the request too, and its next attribute read would
        need a lazy load that an async session cannot run.

        :raises BookBusyError: If SQLite's write lock was not free within the busy timeout.
        :raises NoChangeOpenError: If a row of the adapter's tables was left pending outside a block.
        """
        if self._session.in_transaction():
            await self._session.commit()
        for row in list(self._session.identity_map.values()):
            if isinstance(row, DefaultBase):
                self._session.expire(row)
        try:
            await self._session.connection(execution_options={CHANGE_BLOCK_OPTION: True})
        except OperationalError as error:
            if isinstance(error.orig, SqliteOperationalError) and error.orig.sqlite_errorname == SQLITE_BUSY:
                raise BookBusyError from error
            raise

    def _guard_flush(self, session: Session, _context: object, _instances: object) -> None:
        """Refuse a flush that writes a row of the adapter's tables while no block is open.

        :param session: Session about to flush.
        :type session: Session
        :param _context: Flush context, required by the event signature and unused.
        :type _context: object
        :param _instances: Objects the flush was asked for, required by the event signature and unused.
        :type _instances: object
        :raises NoChangeOpenError: If a row of the adapter's tables is to be inserted, updated or deleted.
        """
        if self._is_open:
            return
        changed = chain(session.new, session.deleted, (row for row in session.dirty if session.is_modified(row)))
        if any(isinstance(row, DefaultBase) for row in changed):
            raise NoChangeOpenError

    def _guard_statement(self, state: ORMExecuteState) -> None:
        """Refuse a bulk insert, update or delete of the adapter's tables while no block is open.

        :param state: The statement the session is about to execute.
        :type state: ORMExecuteState
        :raises NoChangeOpenError: If the statement writes a table of the adapter.
        """
        if self._is_open or not (state.is_insert or state.is_update or state.is_delete):
            return
        if (mapper := state.bind_mapper) is not None and issubclass(mapper.class_, DefaultBase):
            raise NoChangeOpenError

    @override
    async def commit(self) -> None:
        """Commit the transaction, making every change since the last commit durable and visible."""
        await self._session.commit()

    @override
    async def rollback(self) -> None:
        """Roll the transaction back, discarding every change since the last commit."""
        await self._session.rollback()
