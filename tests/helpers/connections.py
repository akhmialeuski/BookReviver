"""Counting the connections of the SQL database that are in use, for the tests of what holds one."""

from typing import TYPE_CHECKING

from sqlalchemy.pool import QueuePool

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase


def checked_out_connections(database: SqlDatabase) -> int:
    """Count the connections that a session or a block took from the pool of the database and has not given back.

    :param database: SQL database over a file, whose engine keeps a queue pool.
    :type database: SqlDatabase
    :returns: Number of connections in use at this moment.
    :rtype: int
    """
    pool = database.engine.sync_engine.pool
    assert isinstance(pool, QueuePool)
    return pool.checkedout()
