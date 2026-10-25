"""The parts of a book a transaction wrote, found from the tables its statements wrote rows of.

A reader of a book asks for parts of it, ``BookResource``, and a change of the book names the parts it wrote, so the
reader reads only those again. The tables are the one thing every write goes through, whether a repository adds an
entity, updates many in one flush or deletes rows with one statement, so the parts are found from the tables of the
written rows rather than declared by each repository. ``WrittenParts`` listens to the session of a transaction: after
a flush it takes the tables of the new, changed and deleted rows, and it runs every bulk statement itself to take the
table of one that wrote at least one row.

The place where the reader left the book is no part a reader asks for again, since it changes as the reader moves, so
its table maps to none, and a change that wrote only the place leaves the version of the book as it was.
"""

from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import event
from sqlalchemy.engine import CursorResult
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.expression import TableClause

from bookreviver.domain.enums import BookResource

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Result
    from sqlalchemy.orm import ORMExecuteState, Session, UOWTransaction

# The part of the book each table of a book holds, the tables of no part left out
PART_OF_TABLE: Final[Mapping[str, BookResource]] = {
    'book_records': BookResource.RECORD,
    'sources': BookResource.SOURCES,
    'scans': BookResource.SCANS,
    'pages': BookResource.PAGES,
    'pagination_sections': BookResource.PAGES,
    'page_groups': BookResource.PAGES,
    'page_versions': BookResource.VERSIONS,
    'page_stages': BookResource.STAGES,
    'page_step_states': BookResource.SETTINGS,
    'step_values': BookResource.SETTINGS,
    'group_values': BookResource.SETTINGS,
    'step_changes': BookResource.HISTORY,
    'page_history': BookResource.HISTORY,
    'recipes': BookResource.RECIPES,
    'recipe_profiles': BookResource.RECIPES,
}
# The events of the session the parts are found from
AFTER_FLUSH: Final = 'after_flush'
DO_ORM_EXECUTE: Final = 'do_orm_execute'


class WrittenParts:
    """The parts of the book the transaction of one session has written so far.

    :ivar parts: The parts written, in no order.
    """

    def __init__(self, session: Session) -> None:
        """Start listening to ``session``, whose transaction has written nothing yet.

        :param session: Session of a transaction of a book.
        :type session: Session
        """
        self.parts: set[BookResource] = set()
        event.listen(session, AFTER_FLUSH, self._flushed)
        event.listen(session, DO_ORM_EXECUTE, self._executed)

    def _mark(self, table: str) -> None:
        """Note the part of the book a table holds, if it holds one.

        :param table: Name of a table a statement wrote rows of.
        :type table: str
        """
        if (part := PART_OF_TABLE.get(table)) is not None:
            self.parts.add(part)

    def _flushed(self, session: Session, _context: UOWTransaction) -> None:
        """Note the parts of the rows a flush inserted, updated or deleted.

        :param session: The session that flushed, whose new, changed and deleted rows are still listed.
        :type session: Session
        :param _context: The flush, which the listener does not need.
        :type _context: UOWTransaction
        """
        for row in (*session.new, *session.dirty, *session.deleted):
            self._mark(row.__table__.name)

    def _executed(self, state: ORMExecuteState) -> Result[Any] | None:
        """Run a bulk insert, update or delete, and note its part when it wrote a row.

        :param state: The statement about to run.
        :type state: ORMExecuteState
        :returns: The result of a write the listener ran itself, or None to let the session run any other statement.
        :rtype: Result[Any] | None
        """
        statement = state.statement
        if not isinstance(statement, UpdateBase):
            return None
        result = state.invoke_statement()
        # An ORM statement with RETURNING answers with its rows and no count, so it is taken to have written
        wrote = not isinstance(result, CursorResult) or result.rowcount != 0
        # The writes of a book name one table each, never a join or an alias
        if wrote and isinstance(table := statement.table, TableClause):
            self._mark(table.name)
        return result
