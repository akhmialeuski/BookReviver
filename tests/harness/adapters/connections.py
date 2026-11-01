"""Counting the connections of the SQL database that are in use, for the tests of what holds one."""

from typing import TYPE_CHECKING

from sqlalchemy.pool import QueuePool

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase


def checked_out_connections(database: SqlDatabase) -> int:
    """Count the connections that a transaction took from the pools of the database and has not given back.

    :param database: SQL database over a file, whose engines keep a queue pool each.
    :type database: SqlDatabase
    :returns: Number of connections in use at this moment, in the pool of the writer and the pool of the readers.
    :rtype: int
    """
    pools = [database.engine.pool, database.read_engine.pool]
    assert all(isinstance(pool, QueuePool) for pool in pools)
    return sum(pool.checkedout() for pool in pools if isinstance(pool, QueuePool))
