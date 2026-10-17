"""Tests that an open event stream holds no connection of the database pool of the SQLite file.

The stream of a book stays open for as long as a browser tab does, and the session of its request closes only when the
stream ends, so a read that the request left in a transaction would keep one of the pool's connections all that time.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.api.route_names import RouteName
from bookreviver.domain.events import ProjectChanged
from tests.helpers.builders import make_project
from tests.helpers.connections import checked_out_connections
from tests.helpers.seeding import commit_account, store_project
from tests.helpers.sse import STREAM_TIMEOUT_SECONDS, EventStreamReader

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, Project
    from bookreviver.domain.ids import AccountId
    from tests.helpers.fakes_jobs import JobFakes

pytestmark = pytest.mark.anyio

# Browser tabs of one book that are open at the same time
OPEN_TABS: int = 3


async def _store_project_of(database: SqlDatabase, owner_id: AccountId) -> Project:
    """Commit a project of an account to the SQL database.

    :param database: SQL database of the running application.
    :type database: SqlDatabase
    :param owner_id: Committed account that owns the project.
    :type owner_id: AccountId
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=owner_id)
    await store_project(SqlAlchemyUnitOfWork(database), project)
    return project


@pytest.fixture
async def fx_owned_project(fx_database: SqlDatabase, fx_actor: Actor) -> Project:
    """Commit a project of the signed-in account, which ``fx_database`` stores.

    :param fx_database: SQL database of the test, which the running application runs on, whose pool a test counts the
                        connections of.
    :type fx_database: SqlDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored project of the signed-in account.
    :rtype: Project
    """
    return await _store_project_of(fx_database, fx_actor.account_id)


@pytest.fixture
async def fx_strangers_project(fx_database: SqlDatabase) -> Project:
    """Commit a project of another account.

    :param fx_database: SQL database of the test, which the running application runs on.
    :type fx_database: SqlDatabase
    :returns: The stored project of the other account.
    :rtype: Project
    """
    return await _store_project_of(fx_database, await commit_account(fx_database))


class TestStreamConnections:
    """Tests for the connections of the pool that GET /projects/{project_id}/events holds."""

    async def test_open_streams_of_one_book_hold_no_connection(
        self, fx_app: FastAPI, fx_fakes: JobFakes, fx_database: SqlDatabase, fx_owned_project: Project
    ) -> None:
        """Verify three streams of one book that are open at once leave the connections in use where they were.

        The count is read once the third subscription is in place, which is after the owner check of every request,
        so each stream is open and the connection of its check would be held if the read were left in a transaction.
        The streams then end by one event that all of them receive.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_fakes: Adapters the application runs on, whose event bus counts the open subscriptions.
        :type fx_fakes: JobFakes
        :param fx_database: SQL database of the running application.
        :type fx_database: SqlDatabase
        :param fx_owned_project: Project of the signed-in account, which the streams listen to.
        :type fx_owned_project: Project
        """
        path = fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_owned_project.id)
        readers = [EventStreamReader(count=1) for _ in range(OPEN_TABS)]
        before = checked_out_connections(fx_database)
        # A list, so that a test that never reached the measurement fails instead of comparing a default
        while_open: list[int] = []
        async with anyio.create_task_group() as group:
            for reader in readers:
                group.start_soon(reader.read, fx_app, path)
            with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
                await fx_fakes.events.wait_for_subscriptions(OPEN_TABS)
            while_open.append(checked_out_connections(fx_database))
            await fx_fakes.events.publish(ProjectChanged(project_id=fx_owned_project.id))

        expect(while_open == [before])
        expect([reader.status for reader in readers] == [HTTPStatus.OK] * OPEN_TABS)
        expect(fx_fakes.events.open_subscriptions == 0)
        expect(checked_out_connections(fx_database) == before)
        assert_expectations()

    async def test_project_of_another_account_is_not_found_before_any_event(
        self,
        fx_app: FastAPI,
        fx_client: httpx.AsyncClient,
        fx_fakes: JobFakes,
        fx_database: SqlDatabase,
        fx_strangers_project: Project,
    ) -> None:
        """Verify the owner check still refuses another account's book before the release, and holds nothing.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on, whose event bus records a subscription if one is made.
        :type fx_fakes: JobFakes
        :param fx_database: SQL database of the running application.
        :type fx_database: SqlDatabase
        :param fx_strangers_project: Project of another account.
        :type fx_strangers_project: Project
        """
        path = fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_strangers_project.id)
        before = checked_out_connections(fx_database)
        # A stream that started by mistake would never end, so the request is bounded
        with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
            response = await fx_client.get(path)

        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(not fx_fakes.events.subscribed.is_set())
        expect(checked_out_connections(fx_database) == before)
        assert_expectations()
