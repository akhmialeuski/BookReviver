"""Counting the SQL statements an engine sends, for the tests that prove a use case costs the same for any number of rows."""

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
