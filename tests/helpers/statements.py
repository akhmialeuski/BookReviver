"""Counting and recording the SQL statements an engine sends, for the tests that prove what a use case sends."""

from typing import TYPE_CHECKING, Any, Self

from sqlalchemy import event

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# The key of the statement text among the named arguments of the event
STATEMENT_KEY: str = 'statement'

# The engine event fired once for every statement sent to the database
STATEMENT_EVENT: str = 'before_cursor_execute'


class StatementCounter:
    """Count the SQL statements an engine sends while the counter is attached.

    :ivar count: Number of statements sent so far.
    """

    def __init__(self) -> None:
        """Start at no statements."""
        self.count = 0

    def __call__(self, *_args: object) -> None:
        """Count one statement, whatever the arguments of the ``before_cursor_execute`` event.

        :param _args: Connection, cursor, statement, parameters, context and the executemany flag, all unused.
        :type _args: object
        """
        self.count += 1


class StatementLog:
    """Record the text of the SQL statements an engine sends while the log is attached, in the order they are sent.

    :ivar statements: The statements sent so far.
    """

    def __init__(self, engine: Engine) -> None:
        """Prepare to record the statements of an engine.

        :param engine: Synchronous engine under the async engine, whose statements are recorded.
        :type engine: Engine
        """
        self._engine = engine
        self.statements: list[str] = []

    def __enter__(self) -> Self:
        """Start recording.

        :returns: The log itself.
        :rtype: Self
        """
        event.listen(self._engine, STATEMENT_EVENT, self._record, named=True)
        return self

    def __exit__(self, *exception: object) -> None:
        """Stop recording.

        :param exception: The exception the block raised, if any.
        :type exception: object
        """
        event.remove(self._engine, STATEMENT_EVENT, self._record)

    def _record(self, **arguments: Any) -> None:
        """Record one statement.

        :param arguments: What SQLAlchemy tells of the statement, by name, of which its text is kept.
        :type arguments: Any
        """
        self.statements.append(arguments[STATEMENT_KEY])
