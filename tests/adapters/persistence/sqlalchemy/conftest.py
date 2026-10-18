"""Fixtures giving the SQLAlchemy adapter tests an owner and the import rig over the root ``fx_database``.

The tests of the write blocks get a database whose blocks are timed by a tracer that tells them when a block asks for
the writer, a second database over the same file whose blocks give up almost at once, and a project to change.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.values import JobEnd
from tests.helpers.builders import make_project
from tests.helpers.database import copy_schema_template, database_file
from tests.helpers.fakes_imports import DEFAULT_PARALLEL_SCANS, ImportRig
from tests.helpers.fakes_jobs import JobFakes
from tests.helpers.seeding import commit_account, store_project
from tests.helpers.tracers import AskingTracer
from tests.helpers.wait_limits import IMPATIENT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Job, Project
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.runtime import JobControl
    from tests.helpers.database import UnitOfWorkFactory

# Long enough that a block which wrongly waits for a lock that is never given fails the test, and does not hang it
TRACED_WAIT_SECONDS: float = 10.0
STORAGE_DIRECTORY: str = 'storage'


@pytest.fixture
async def fx_owner_id(fx_database: SqlDatabase) -> AccountId:
    """Commit an account that may own projects, which the owner key of the projects table requires.

    :param fx_database: Fresh SQLite database with every table created.
    :type fx_database: SqlDatabase
    :returns: Identifier of the committed account.
    :rtype: AccountId
    """
    return await commit_account(fx_database)


@pytest.fixture
def fx_import_rig(fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, tmp_path: Path) -> Callable[..., ImportRig]:
    """Give a function that builds the import rig over the database, whose board runs only the import jobs.

    The board of an ``ImportRig`` runs every job as an import. The rig this function builds cuts as many scans at once
    as a test asks for, and lets any job that is not an import end at once, since the split an import queues is not
    the subject of these tests.

    :param fx_database: Fresh SQLite database with every table created.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a unit of work over ``fx_database``.
    :type fx_uow: UnitOfWorkFactory
    :param tmp_path: Temporary directory of the test, which holds the storage root of the rig.
    :type tmp_path: Path
    :returns: Function taking the largest number of scans cut at once and returning the rig.
    :rtype: Callable[..., ImportRig]
    """

    def build(parallel_scans: int = DEFAULT_PARALLEL_SCANS) -> ImportRig:
        """Build the rig over a new storage root and the database.

        :param parallel_scans: Largest number of scans the import cuts at once.
        :type parallel_scans: int
        :returns: The rig, whose board runs an import on the database.
        :rtype: ImportRig
        """
        base = ImportRig.build(tmp_path / STORAGE_DIRECTORY, fx_database, fx_uow)
        # The runner needs the service of the rig and the rig needs the runner, so the rig is found when a job runs
        built: list[ImportRig] = []

        async def run(job: Job, control: JobControl) -> JobEnd:
            """Run an import job on the database, and let any other job succeed at once.

            :param job: The job the board took from its queue.
            :type job: Job
            :param control: What the job reports through.
            :type control: JobControl
            :returns: How the job ended.
            :rtype: JobEnd
            """
            if job.kind is not JobKind.IMPORT_SOURCE:
                return JobEnd(state=JobState.SUCCEEDED)
            return await built[0].service(parallel_scans=parallel_scans).run_import(job, control)

        rig = evolve(base, fakes=JobFakes(events=base.events, clock=base.fakes.clock, runner=run))
        built.append(rig)
        return rig

    return build


@pytest.fixture
def fx_tracer() -> AskingTracer:
    """Build the tracer of the test, which every database of the test is given.

    :returns: A tracer with no span recorded.
    :rtype: AskingTracer
    """
    return AskingTracer()


@pytest.fixture
async def fx_traced_database(
    fx_settings: Settings, fx_schema_template: Path, fx_tracer: AskingTracer
) -> AsyncIterator[SqlDatabase]:
    """Open a database over a fresh file, whose blocks are timed by the tracer of the test.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :param fx_schema_template: File with every table at the head revision.
    :type fx_schema_template: Path
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the database and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    await copy_schema_template(fx_schema_template, database_file(fx_settings))
    database = SqlDatabase(fx_settings.resolved_database_url, tracer=fx_tracer, wait_seconds=TRACED_WAIT_SECONDS)
    yield database
    await database.dispose()


@pytest.fixture
async def fx_open_database(
    fx_traced_database: SqlDatabase, fx_tracer: AskingTracer
) -> AsyncIterator[Callable[[float], SqlDatabase]]:
    """Give a function that opens another database over the file of the traced one, with the busy timeout it is told.

    Each database has a write queue of its own, so its blocks meet those of the traced database at the busy timeout of
    SQLite, as the blocks of another process do, and meet each other in its queue. The busy timeout is the one every
    connection of the database opens with.

    :param fx_traced_database: The database whose file and schema the others share.
    :type fx_traced_database: SqlDatabase
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the opening function and disposing of every engine it opened afterwards.
    :rtype: AsyncIterator[Callable[[float], SqlDatabase]]
    """
    url = fx_traced_database.engine.url.render_as_string(hide_password=False)
    opened: list[SqlDatabase] = []

    def open_database(wait_seconds: float) -> SqlDatabase:
        """Open a database over the shared file.

        :param wait_seconds: The busy timeout every connection of the database opens with.
        :type wait_seconds: float
        :returns: The database, which the fixture disposes of.
        :rtype: SqlDatabase
        """
        opened.append(SqlDatabase(url, tracer=fx_tracer, wait_seconds=wait_seconds))
        return opened[-1]

    yield open_database
    for database in opened:
        await database.dispose()


@pytest.fixture
def fx_impatient_database(fx_open_database: Callable[[float], SqlDatabase]) -> SqlDatabase:
    """Open a second database over the file of the first, for the units of work that give up for the lock at once.

    The second database has a write queue of its own, so its blocks meet those of the first at the busy timeout of
    SQLite, as the blocks of another process do, and meet each other in its queue. The limit of a block is that of its
    unit of work, so a test that wants a refusal opens its unit of work with ``IMPATIENT_SECONDS``.

    :param fx_open_database: Function that opens a database over the file of the traced one.
    :type fx_open_database: Callable[[float], SqlDatabase]
    :returns: The database, which ``fx_open_database`` disposes of afterwards.
    :rtype: SqlDatabase
    """
    return fx_open_database(IMPATIENT_SECONDS)


@pytest.fixture
async def fx_project(fx_traced_database: SqlDatabase, fx_tracer: AskingTracer) -> Project:
    """Commit an account and a project of it, which a ``change_book`` block needs to find.

    The blocks that store the project are spans too, which are not what a test looks at, so they are forgotten.

    :param fx_traced_database: Database to store into.
    :type fx_traced_database: SqlDatabase
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: The project.
    :rtype: Project
    """
    project = make_project(owner_id=await commit_account(fx_traced_database))
    await store_project(SqlAlchemyUnitOfWork(fx_traced_database), project)
    fx_tracer.finished.clear()
    return project
