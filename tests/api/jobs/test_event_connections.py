"""Tests that an open event stream holds no connection of the database pool of the SQLite file.

The stream of a book stays open for as long as a browser tab does, and the session of its request closes only when the
stream ends, so a read that the request left in a transaction would keep one of the pool's connections all that time.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.api.route_names import RouteName
from tests.helpers.builders import make_book_change, make_record
from tests.helpers.connections import checked_out_connections
from tests.helpers.seeding import commit_account, store_book
from tests.helpers.sse import STREAM_TIMEOUT_SECONDS, EventStreamReader

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import UnitOfWork
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import JobFakes

pytestmark = pytest.mark.anyio

# Browser tabs of one book that are open at the same time
OPEN_TABS: int = 3


async def _store_book_of(uow: UnitOfWork, owner_id: AccountId) -> BookRecord:
    """Commit a book of an account to the catalogue and the books of the test.

    :param uow: Unit of work over the catalogue and the books of the running application.
    :type uow: UnitOfWork
    :param owner_id: Committed account that owns the book.
    :type owner_id: AccountId
    :returns: The stored book.
    :rtype: BookRecord
    """
    book_record = make_record()
    await store_book(uow, book_record, owner=owner_id)
    return book_record


@pytest.fixture
async def fx_owned_book(fx_uow: UnitOfWorkFactory, fx_actor: Actor) -> BookRecord:
    """Commit a book of the signed-in account, which the catalogue of the test stores.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book of the signed-in account.
    :rtype: BookRecord
    """
    return await _store_book_of(fx_uow(), fx_actor.account_id)


@pytest.fixture
async def fx_strangers_book(fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory) -> BookRecord:
    """Commit a book of another account.

    :param fx_database: Catalogue of the test, which the running application runs on.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :returns: The stored book of the other account.
    :rtype: BookRecord
    """
    return await _store_book_of(fx_uow(), await commit_account(fx_database))


class TestStreamConnections:
    """Tests for the connections of the pool that GET /books/{book_id}/events holds."""

    async def test_open_streams_of_one_book_hold_no_connection(
        self, fx_app: FastAPI, fx_fakes: JobFakes, fx_database: SqlDatabase, fx_owned_book: BookRecord
    ) -> None:
        """Verify three streams of one book that are open at once leave the connections in use where they were.

        The count is read once every stream sent the version it opens with, which is after the owner check and the read
        of the version of every request, so each stream is open and the connection of a read would be held if the read
        were left in a transaction.
        The streams then end by one event that all of them receive.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_fakes: Adapters the application runs on, whose event bus counts the open subscriptions.
        :type fx_fakes: JobFakes
        :param fx_database: SQL database of the running application.
        :type fx_database: SqlDatabase
        :param fx_owned_book: Book of the signed-in account, which the streams listen to.
        :type fx_owned_book: BookRecord
        """
        path = fx_app.url_path_for(RouteName.BOOK_EVENTS, book_id=fx_owned_book.id)
        readers = [EventStreamReader(count=1) for _ in range(OPEN_TABS)]
        before = checked_out_connections(fx_database)
        # A list, so that a test that never reached the measurement fails instead of comparing a default
        while_open: list[int] = []
        async with anyio.create_task_group() as group:
            for reader in readers:
                group.start_soon(reader.read, fx_app, path)
            with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
                await fx_fakes.events.wait_for_subscriptions(OPEN_TABS)
                # Each stream reads the version of the book once it subscribed, and is open once it sent it
                for reader in readers:
                    await reader.opened.wait()
            while_open.append(checked_out_connections(fx_database))
            await fx_fakes.events.publish(make_book_change(book_id=fx_owned_book.id))

        expect(while_open == [before])
        expect([reader.status for reader in readers] == [HTTPStatus.OK] * OPEN_TABS)
        expect(fx_fakes.events.open_subscriptions == 0)
        expect(checked_out_connections(fx_database) == before)
        assert_expectations()

    async def test_book_of_another_account_is_not_found_before_any_event(
        self,
        fx_app: FastAPI,
        fx_client: httpx.AsyncClient,
        fx_fakes: JobFakes,
        fx_database: SqlDatabase,
        fx_strangers_book: BookRecord,
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
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: BookRecord
        """
        path = fx_app.url_path_for(RouteName.BOOK_EVENTS, book_id=fx_strangers_book.id)
        before = checked_out_connections(fx_database)
        # A stream that started by mistake would never end, so the request is bounded
        with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
            response = await fx_client.get(path)

        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(not fx_fakes.events.subscribed.is_set())
        expect(checked_out_connections(fx_database) == before)
        assert_expectations()
