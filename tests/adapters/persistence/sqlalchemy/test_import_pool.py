"""Test of the connection pools during an import that cuts several scans at once, on the SQLite file of a book.

Up to ``parallel_scans`` coroutines cut the scans of a job, and all of them write through the one catalogue database of the
job. A change takes its connection only when it has the writer, and a change that waits for the writer holds none, so
the pool of the writer, which holds one connection, is never asked for a second and no connection is left behind.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import define
from delayed_assert import assert_expectations, expect
from sqlalchemy import event
from sqlalchemy.pool import QueuePool

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.values import SliceRequest
from tests.helpers.builders import directory_of, make_record, open_for
from tests.helpers.connections import checked_out_connections
from tests.helpers.fakes_imports import IMPORT_TIMEOUT_SECONDS, pdf_upload
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.pool import ConnectionPoolEntry, PoolProxiedConnection

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId
    from tests.helpers.builders import BookOpener
    from tests.helpers.fakes_imports import ImportRig

pytestmark = pytest.mark.anyio

PARALLEL_SCANS: int = 4
BOOK_PDF: str = 'book.pdf'
SAMPLES_DIRECTORY: str = 'samples'
POOL_CHECKOUT: str = 'checkout'
POOL_CHECKIN: str = 'checkin'
ONE_CONNECTION: int = 1


@define
class ConnectionPeak:
    """The number of connections checked out of a pool, and the largest it has been.

    :ivar checked_out: Number of connections checked out now.
    :ivar peak: Largest number of connections checked out at the same time.
    """

    checked_out: int = 0
    peak: int = 0

    def on_checkout(
        self, _dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry, _proxy: PoolProxiedConnection
    ) -> None:
        """Count a connection taken from the pool, and keep the largest count.

        :param _dbapi_connection: Connection of the driver, required by the event signature and unused.
        :type _dbapi_connection: DBAPIConnection
        :param _record: Pool entry of the connection, required by the event signature and unused.
        :type _record: ConnectionPoolEntry
        :param _proxy: Proxy the caller of the pool receives, required by the event signature and unused.
        :type _proxy: PoolProxiedConnection
        """
        self.checked_out += 1
        self.peak = max(self.peak, self.checked_out)

    def on_checkin(self, _dbapi_connection: DBAPIConnection | None, _record: ConnectionPoolEntry) -> None:
        """Count a connection given back to the pool.

        :param _dbapi_connection: Connection of the driver, or None when it was invalidated; unused.
        :type _dbapi_connection: DBAPIConnection | None
        :param _record: Pool entry of the connection, required by the event signature and unused.
        :type _record: ConnectionPoolEntry
        """
        self.checked_out -= 1


@contextmanager
def _counting_connections(database: SqlDatabase) -> Iterator[ConnectionPeak]:
    """Count the connections checked out of the pool of the writer of a database, until the block ends.

    The count comes from the ``checkout`` and ``checkin`` events of the pool, so every moment is seen and not only the
    ones a test samples. The listeners are removed afterwards.

    :param database: SQLite database of a book, whose writer pool is counted.
    :type database: SqlDatabase
    :returns: Iterator yielding the counter and removing its listeners afterwards.
    :rtype: Iterator[ConnectionPeak]
    """
    counter = ConnectionPeak()
    pool = database.engine.pool
    event.listen(pool, POOL_CHECKOUT, counter.on_checkout)
    event.listen(pool, POOL_CHECKIN, counter.on_checkin)
    try:
        yield counter
    finally:
        event.remove(pool, POOL_CHECKOUT, counter.on_checkout)
        event.remove(pool, POOL_CHECKIN, counter.on_checkin)


class TestRunImportConnections:
    """Tests for the pooled connections ImportService.run_import() takes while it cuts scans at once."""

    async def test_four_cutters_never_hold_more_than_one_pooled_connection(
        self,
        fx_books: BookRegistry,
        fx_owner_id: AccountId,
        fx_import_rig: Callable[..., ImportRig],
        tmp_path: Path,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify an import with four cutters at once checks out one writer connection at a time, with all scans ready.

        The rasterizer holds every write until all four cutters are writing, so they leave their writes together and
        each of them then asks for its change of the one catalogue database at the same moment. A pause would not do: a
        change on a SQLite file outlasts it, and the cutters would pass their writes one after another. The changes
        queue for the writer of the book, and a change that waits holds no connection, so the writer pool of the book
        shows one connection in use at a time. A change that opened its session before it had the writer would hold a
        connection while it waits, and the peak assertion would fail.

        :param fx_books: Registry of the books of the test, which gives the database of the book whose pool is counted.
        :type fx_books: BookRegistry
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_import_rig: Function building the import rig whose board runs an import on the database.
        :type fx_import_rig: Callable[..., ImportRig]
        :param tmp_path: Temporary directory of the test, holding the sample file.
        :type tmp_path: Path
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        rig = fx_import_rig(PARALLEL_SCANS)
        all_writing = anyio.Event()

        async def hold_until_all_write(_number: int) -> None:
            """Hold a write until every cutter is writing, and release them all with the last one.

            :param _number: Number of the scan about to be written, which does not matter here.
            :type _number: int
            """
            if rig.rasterizer.running == PARALLEL_SCANS:
                all_writing.set()
            await all_writing.wait()

        rig.rasterizer.before_extract = hold_until_all_write
        samples = tmp_path / SAMPLES_DIRECTORY
        samples.mkdir()
        owner = Actor(account_id=fx_owner_id)
        catalogue = fx_open_book.catalogue()
        book_id = await store_book(catalogue, make_record(), owner=fx_owner_id, databases=fx_open_book.databases)
        async with fx_books.use(book_id, fx_open_book.files.directory(directory_of(book_id)).database) as book:
            with _counting_connections(book) as peak:
                job = await rig.service(catalogue=catalogue).start_import(
                    open_for(owner, book_id, databases=rig.databases, files=rig.files),
                    [pdf_upload(samples, BOOK_PDF, pages=PARALLEL_SCANS)],
                )
                with anyio.fail_after(IMPORT_TIMEOUT_SECONDS):
                    await rig.fakes.board.run_queued()
            in_use = checked_out_connections(book)
            # A file database gets a queue pool, so the limit of the pool is the real one
            queued = isinstance(book.engine.pool, QueuePool)

        stored = rig.stored_job(job)
        scans = await fx_open_book.databases.open(book_id).read(
            lambda repos: repos.scans.list_window(SliceRequest(limit=PARALLEL_SCANS)).items
        )
        expect(queued)
        expect(rig.rasterizer.peak == PARALLEL_SCANS)
        expect(stored.state is JobState.SUCCEEDED)
        expect(len(scans) == PARALLEL_SCANS and all(scan.renditions.ready for scan in scans))
        expect(peak.peak == ONE_CONNECTION)
        expect(peak.checked_out == 0)
        expect(in_use == 0, 'a connection of either pool of the book was left checked out')
        expect(checked_out_connections(rig.database) == 0, 'a connection of either pool of the catalogue was left')
        assert_expectations()
