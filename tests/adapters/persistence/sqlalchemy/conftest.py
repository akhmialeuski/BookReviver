"""Fixtures giving the SQLAlchemy adapter tests a fresh SQLite database at the head revision, and an owner.

The tests of the write blocks get a database whose blocks are timed by a tracer that tells them when a block asks for
the lock, a second database over the same file whose blocks give up almost at once, and a project to change.
"""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.app.container import build_container
from bookreviver.app.settings import PersistenceBackend
from tests.helpers.builders import make_project
from tests.helpers.schema import create_schema
from tests.helpers.seeding import commit_account, store_project
from tests.helpers.tracers import AskingTracer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import AccountId

# Long enough that a block which wrongly waits for a lock that is never given fails the test, and does not hang it
TRACED_WAIT_SECONDS: float = 10.0
# Short enough that a block refused the lock gives up at once
IMPATIENT_SECONDS: float = 0.05


@pytest.fixture
async def fx_database(fx_settings: Settings) -> AsyncIterator[SqlDatabase]:
    """Open the database through the application's own container, built for the SQL backend.

    Building the whole container, not only the database provider, also checks that the production dependency graph
    with this backend resolves. The schema is created from the models and recorded at the head revision first.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the open database and closing it with the container afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    await create_schema(fx_settings)
    container = build_container(fx_settings.model_copy(update={'persistence': PersistenceBackend.SQLALCHEMY}))
    yield await container.get(SqlDatabase)
    await container.close()


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
def fx_tracer() -> AskingTracer:
    """Build the tracer of the test, which every database of the test is given.

    :returns: A tracer with no span recorded.
    :rtype: AskingTracer
    """
    return AskingTracer()


@pytest.fixture
async def fx_traced_database(fx_settings: Settings, fx_tracer: AskingTracer) -> AsyncIterator[SqlDatabase]:
    """Open a database over a fresh file, whose blocks are timed by the tracer of the test.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the database and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    await create_schema(fx_settings)
    database = SqlDatabase(fx_settings.resolved_database_url, tracer=fx_tracer, wait_seconds=TRACED_WAIT_SECONDS)
    yield database
    await database.dispose()


@pytest.fixture
async def fx_impatient_database(fx_traced_database: SqlDatabase, fx_tracer: AskingTracer) -> AsyncIterator[SqlDatabase]:
    """Open a second database over the file of the first, for the units of work that give up for the lock at once.

    The second database has a write queue of its own, so its blocks meet those of the first at the busy timeout of
    SQLite, as the blocks of another process do, and meet each other in its queue. The limit of a block is that of its
    unit of work, so a test that wants a refusal opens its unit of work with ``IMPATIENT_SECONDS``.

    :param fx_traced_database: The database whose file and schema the second one shares.
    :type fx_traced_database: SqlDatabase
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the database and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    url = fx_traced_database.engine.url.render_as_string(hide_password=False)
    database = SqlDatabase(url, tracer=fx_tracer, wait_seconds=IMPATIENT_SECONDS)
    yield database
    await database.dispose()


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
    async with fx_traced_database.sessions() as session:
        await store_project(SqlAlchemyUnitOfWork(session), project)
    fx_tracer.finished.clear()
    return project
