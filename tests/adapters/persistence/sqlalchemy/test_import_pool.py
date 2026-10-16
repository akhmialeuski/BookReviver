"""Test of the connection pools during an import that cuts several scans at once, on one SQLite file.

Up to ``parallel_scans`` coroutines cut the scans of a job, and all of them write through the one unit of work of the
job. A change takes its connection only when it has the writer, and a change that waits for the writer holds none, so
the pool of the writer, which holds one connection, is never asked for a second and no connection is left behind.
"""

from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import define
from delayed_assert import assert_expectations, expect
from sqlalchemy import event
from sqlalchemy.pool import QueuePool

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.values import SliceRequest
from tests.helpers.builders import make_project
from tests.helpers.connections import checked_out_connections
from tests.helpers.fakes_imports import IMPORT_TIMEOUT_SECONDS, ImportRig, pdf_upload
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.pool import ConnectionPoolEntry, PoolProxiedConnection

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

PARALLEL_SCANS: int = 4
BOOK_PDF: str = 'book.pdf'
STORAGE_DIRECTORY: str = 'storage'
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


@pytest.fixture
def fx_connection_peak(fx_database: SqlDatabase) -> Iterator[ConnectionPeak]:
    """Count the connections checked out of the pool of the database, until the test is over.

    The count comes from the ``checkout`` and ``checkin`` events of the pool, so every moment is seen and not only the
    ones a test samples. The listeners are removed afterwards.

    :param fx_database: Fresh SQLite database with every table created.
    :type fx_database: SqlDatabase
    :returns: Iterator yielding the counter and removing its listeners afterwards.
    :rtype: Iterator[ConnectionPeak]
    """
    counter = ConnectionPeak()
    pool = fx_database.engine.pool
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
        fx_database: SqlDatabase,
        fx_owner_id: AccountId,
        fx_connection_peak: ConnectionPeak,
        tmp_path: Path,
    ) -> None:
        """Verify an import with four cutters at once checks out one writer connection at a time, with all scans ready.

        The rasterizer holds every write until all four cutters are writing, so they leave their writes together and
        each of them then asks for its change of the one unit of work at the same moment. A pause would not do: a
        change on a SQLite file outlasts it, and the cutters would pass their writes one after another. The changes
        queue for the writer, and a change that waits holds no connection, so the writer pool shows one connection in
        use at a time. A change that opened its session before it had the writer would hold a connection while it
        waits, and the peak assertion would fail.

        :param fx_database: Fresh SQLite database with every table created, which gives a file its queue pool.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param fx_connection_peak: Counter of the connections checked out of the pool of the writer.
        :type fx_connection_peak: ConnectionPeak
        :param tmp_path: Temporary directory of the test, holding the storage root and the sample file.
        :type tmp_path: Path
        """
        rig = ImportRig.build(tmp_path / STORAGE_DIRECTORY)
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
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project)
        job = await rig.service(uow=uow).start_import(
            owner, project.id, [pdf_upload(samples, BOOK_PDF, pages=PARALLEL_SCANS)]
        )

        with anyio.fail_after(IMPORT_TIMEOUT_SECONDS):
            await rig.service(uow=uow, parallel_scans=PARALLEL_SCANS).run_import(job.id)

        stored, scans = await uow.read(
            lambda repos: (
                repos.jobs.get(job.id),
                repos.scans.list_for_project(project.id, SliceRequest(limit=PARALLEL_SCANS)).items,
            )
        )
        # A file database gets a queue pool, which an in-memory one does not, so the limit of the pool is the real one
        expect(isinstance(fx_database.engine.pool, QueuePool))
        expect(rig.rasterizer.peak == PARALLEL_SCANS)
        expect(stored.state is JobState.SUCCEEDED)
        expect(len(scans) == PARALLEL_SCANS and all(scan.renditions.ready for scan in scans))
        expect(fx_connection_peak.peak == ONE_CONNECTION)
        expect(fx_connection_peak.checked_out == 0)
        expect(checked_out_connections(fx_database) == 0, 'a connection of either pool was left checked out')
        assert_expectations()
