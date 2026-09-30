"""Tests for the migrations: the baseline builds the schema of the models, and a migration keeps the rows of a book.

Alembic runs every command in an event loop of its own, which ``env.py`` starts with ``asyncio.run``, so the tests
run commands in a worker thread through ``_migrate``.
"""

import shutil
from typing import TYPE_CHECKING

import pytest
from advanced_alchemy.alembic.commands import AlembicCommands
from alembic.script import ScriptDirectory
from asyncer import asyncify
from attrs import evolve
from delayed_assert import assert_expectations, expect
from sqlalchemy import func, inspect, select

from bookreviver.adapters.persistence.sqlalchemy.database import MIGRATIONS_DIR, SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    JobRow,
    PageRow,
    PageVersionRow,
    ProjectRow,
    ScanRow,
    SourceRow,
)
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.helpers.builders import make_job, make_page, make_page_version, make_project, make_scan, make_source
from tests.helpers.seeding import commit_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from advanced_alchemy.base import CommonTableAttributes

    from bookreviver.app.settings import Settings
    from bookreviver.domain.ids import ProjectId

pytestmark = pytest.mark.anyio

# A revision a test adds after the head of the shipped migrations, with the body of its upgrade filled in
REVISION_TEMPLATE: str = '''"""A revision a test adds after the baseline."""

import sqlalchemy as sa
from alembic import op

revision = 'test_revision'
down_revision = {head!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
{upgrade}


def downgrade() -> None:
    pass
'''
# Adds a column to projects, which batch mode on SQLite does by copying, dropping and renaming the table
REBUILD_PROJECTS: str = """    with op.batch_alter_table('projects', recreate='always') as batch_op:
        batch_op.add_column(sa.Column('probe', sa.String(), nullable=False, server_default=''))"""
# Deletes every project, which leaves the rows of its book pointing at no project while foreign keys are off
DELETE_PROJECTS: str = "    op.execute('DELETE FROM projects')"
# The tables holding the rows of a book, each of which refers to the project or to a row that does
BOOK_TABLES: tuple[type[CommonTableAttributes], ...] = (ProjectRow, SourceRow, ScanRow, PageRow, PageVersionRow, JobRow)


@pytest.fixture
async def fx_empty_database(fx_settings: Settings) -> AsyncIterator[SqlDatabase]:
    """Open a database with no table, at the URL the settings name.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the database and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    fx_settings.data_dir.mkdir(parents=True)
    database = SqlDatabase(fx_settings.resolved_database_url)
    yield database
    await database.dispose()


async def _migrate(database: SqlDatabase, command: Callable[..., None], *args: str) -> None:
    """Run an Alembic command in a worker thread, after closing the connections the test's event loop opened.

    :param database: Database the command runs on, whose pool is emptied first.
    :type database: SqlDatabase
    :param command: Command of ``AlembicCommands``, such as ``upgrade``.
    :type command: Callable[..., None]
    :param args: Arguments of the command, such as the target revision.
    :type args: str
    """
    await database.dispose()
    await asyncify(command)(*args)


async def _commit_book(database: SqlDatabase) -> ProjectId:
    """Commit a project whose cover is a page cut from a scan, with its source, the page's version and a job.

    :param database: Migrated database to commit into.
    :type database: SqlDatabase
    :returns: Identifier of the project.
    :rtype: ProjectId
    """
    project = make_project(owner_id=await commit_account(database))
    source = make_source(project_id=project.id)
    scan = make_scan(source=source, number=0)
    page = make_page(project_id=project.id, scan=scan)
    async with database.sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add(scan)
        await uow.pages.add(page)
        await uow.page_versions.add(make_page_version(page_id=page.id))
        await uow.jobs.add(make_job(project_id=project.id))
        await uow.projects.update(evolve(project, cover_page_id=page.id))
        await uow.commit()
    return project.id


def _migrations_with_revision(database: SqlDatabase, target: Path, upgrade: str) -> AlembicCommands:
    """Copy the shipped migrations to ``target``, add a revision after their head, and point ``database`` at them.

    :param database: Database whose configuration is pointed at the copied migrations.
    :type database: SqlDatabase
    :param target: Directory the migrations are copied to.
    :type target: Path
    :param upgrade: Indented body of the new revision's ``upgrade``.
    :type upgrade: str
    :returns: Alembic commands over the database and the copied migrations.
    :rtype: AlembicCommands
    """
    scripts = shutil.copytree(MIGRATIONS_DIR, target, ignore=shutil.ignore_patterns('__pycache__'))
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    (scripts / 'versions' / 'test_revision.py').write_text(REVISION_TEMPLATE.format(head=head, upgrade=upgrade))
    database.config.alembic_config.script_location = str(scripts)
    return AlembicCommands(database.config)


class TestBaseline:
    """Tests for the baseline revision, the one head of the shipped migrations."""

    def test_history_has_exactly_one_head(self) -> None:
        """Verify the migrations have one head, so ``upgrade head`` names one schema."""
        assert len(ScriptDirectory(str(MIGRATIONS_DIR)).get_heads()) == 1

    async def test_upgrade_and_downgrade_match_the_models(self, fx_empty_database: SqlDatabase) -> None:
        """Verify upgrading an empty database leaves nothing for autogenerate, and a downgrade and upgrade again too.

        ``alembic check`` raises when the database differs from the tables in any table, column, type, key or index.

        :param fx_empty_database: Database with no table.
        :type fx_empty_database: SqlDatabase
        """
        migrations = fx_empty_database.migrations
        await _migrate(fx_empty_database, migrations.upgrade, 'head')
        await _migrate(fx_empty_database, migrations.check)
        await _migrate(fx_empty_database, migrations.downgrade, 'base')
        async with fx_empty_database.engine.connect() as connection:
            left = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())
        await _migrate(fx_empty_database, migrations.upgrade, 'head')
        await _migrate(fx_empty_database, migrations.check)
        assert left == [fx_empty_database.config.alembic_config.version_table_name]


class TestEnv:
    """Tests for env.py, which runs a migration on SQLite with foreign keys off and checks them afterwards."""

    async def test_batch_rebuild_of_projects_keeps_the_rows_of_its_book(
        self, fx_empty_database: SqlDatabase, tmp_path: Path
    ) -> None:
        """Verify a revision rebuilding projects in batch mode leaves every row of the book and the project's cover.

        With foreign keys on, dropping the old projects table would cascade to the sources, scans, pages, versions
        and jobs.

        :param fx_empty_database: Database with no table.
        :type fx_empty_database: SqlDatabase
        :param tmp_path: Temporary directory of the test, holding the copied migrations.
        :type tmp_path: Path
        """
        await _migrate(fx_empty_database, fx_empty_database.migrations.upgrade, 'head')
        project_id = await _commit_book(fx_empty_database)
        migrations = _migrations_with_revision(fx_empty_database, tmp_path / 'migrations', REBUILD_PROJECTS)
        await _migrate(fx_empty_database, migrations.upgrade, 'head')
        async with fx_empty_database.sessions() as session:
            for table in BOOK_TABLES:
                expect(await session.scalar(select(func.count()).select_from(table)) == 1, table.__tablename__)
            cover_page_id = await session.scalar(select(ProjectRow.cover_page_id).where(ProjectRow.id == project_id))
        expect(cover_page_id is not None)
        assert_expectations()

    async def test_rows_left_without_their_project_fail_the_migration(
        self, fx_empty_database: SqlDatabase, tmp_path: Path
    ) -> None:
        """Verify a revision deleting projects under keys that are off fails on the rows of the book it orphans.

        :param fx_empty_database: Database with no table.
        :type fx_empty_database: SqlDatabase
        :param tmp_path: Temporary directory of the test, holding the copied migrations.
        :type tmp_path: Path
        """
        await _migrate(fx_empty_database, fx_empty_database.migrations.upgrade, 'head')
        await _commit_book(fx_empty_database)
        migrations = _migrations_with_revision(fx_empty_database, tmp_path / 'migrations', DELETE_PROJECTS)
        with pytest.raises(RuntimeError, match='foreign key to no row'):
            await _migrate(fx_empty_database, migrations.upgrade, 'head')
