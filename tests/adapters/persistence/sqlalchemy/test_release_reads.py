"""Tests of ``release_reads`` against a SQLite file: the read of a session holds a pooled connection until released."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.helpers.builders import make_project
from tests.helpers.connections import checked_out_connections
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio


class TestReleaseReads:
    """Tests for the transaction that a read outside a block leaves open on the session."""

    async def test_release_returns_the_connection_of_a_read_to_the_pool(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a read outside a block takes a connection of the pool and ``release_reads`` gives it back.

        The count after the read is checked too, so that the count is shown to see a connection held by a read: a pool
        that always reported the same number would pass the release without proving anything.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            await store_project(SqlAlchemyUnitOfWork(session), project)
        before = checked_out_connections(fx_database)

        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.get(project.id)
            held = checked_out_connections(fx_database)
            in_transaction_after_read = session.in_transaction()
            await uow.release_reads()
            released = checked_out_connections(fx_database)
            in_transaction_after_release = session.in_transaction()

        expect(in_transaction_after_read)
        expect(held == before + 1)
        expect(released == before)
        expect(not in_transaction_after_release)
        assert_expectations()

    async def test_row_loaded_before_the_release_stays_readable(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify the release keeps the account row of the request usable, which a rollback or an expiry would not.

        An async session cannot load an expired attribute lazily, so reading one would fail. The row is read after the
        release, which is what fastapi-users does with the account of the request once the route returned.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account the row of which is loaded.
        :type fx_owner_id: AccountId
        """
        async with fx_database.sessions() as session:
            account = await session.get(AccountTable, fx_owner_id)
            assert account is not None
            await SqlAlchemyUnitOfWork(session).release_reads()
            is_verified = account.is_verified

        assert is_verified
