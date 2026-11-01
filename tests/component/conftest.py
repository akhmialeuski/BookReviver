"""Fixtures of the component tests: the running application, a client talking to it, its container and its files.

The database is the shared ``fx_database``, which the application runs on as well.
"""

from typing import TYPE_CHECKING

import httpx
import pytest
from dishka import AsyncContainer

from bookreviver.api.auth import signed_in_user
from bookreviver.app.main import create_app
from tests.harness.adapters.storage import BookFiles
from tests.harness.builders.processing import InProcessExecutorProvider
from tests.harness.concurrency.board_idle import JobsIdle
from tests.harness.fakes.accounts import SignedInUser

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from typing import Any

    from dishka import Provider
    from fastapi import FastAPI
    from httpx_oauth.oauth2 import BaseOAuth2

    from bookreviver.app.settings import Settings
    from bookreviver.domain.accounts import Actor
    from tests.harness.adapters.database import SharedDatabaseProvider


@pytest.fixture
def fx_signed_out(fx_app: FastAPI) -> None:
    """Take the test's sign-in off, so a request has no session.

    :param fx_app: The running application, whose stub user is removed.
    :type fx_app: FastAPI
    """
    fx_app.dependency_overrides.pop(signed_in_user)


@pytest.fixture
def fx_container(fx_app: FastAPI) -> AsyncContainer:
    """Return the dependency container of the running application.

    :param fx_app: The running application, on whose state dishka keeps its container.
    :type fx_app: FastAPI
    :returns: The application's container, holding its adapters.
    :rtype: AsyncContainer
    """
    container = fx_app.state.dishka_container
    assert isinstance(container, AsyncContainer)
    return container


@pytest.fixture
def fx_idle() -> JobsIdle:
    """Return the wait that a test calls to let the jobs of a book on the application's job board finish.

    The wait opens the job queue and the event channel of the book, which the running application registered.

    :returns: The wait, called with the identifier of the book.
    :rtype: JobsIdle
    """
    return JobsIdle()


@pytest.fixture
def fx_files(fx_container: AsyncContainer, fx_settings: Settings) -> BookFiles:
    """Return the files of imported books in the stores of the running application.

    :param fx_container: Container of the application, which has registered its local storage by the time it runs.
    :type fx_container: AsyncContainer
    :param fx_settings: Settings of the application, which name the storage root.
    :type fx_settings: Settings
    :returns: Files of the test's books, in the application's own stores.
    :rtype: BookFiles
    """
    del fx_container
    return BookFiles(root=fx_settings.storage_root)


TEST_BASE_URL: str = 'http://testserver'


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Return providers that override the application's own; a test module overrides this fixture with fakes.

    :returns: No providers, so the application's own adapters are used.
    :rtype: Sequence[Provider]
    """
    return ()


@pytest.fixture
def fx_social_clients() -> Sequence[BaseOAuth2[Any]] | None:
    """Return the social sign-in clients to offer in place of the configured ones; a test module overrides this.

    :returns: ``None``, so the providers whose credentials are in the settings are offered.
    :rtype: Sequence[BaseOAuth2[Any]] | None
    """
    return None


@pytest.fixture
async def fx_app(
    fx_settings: Settings,
    fx_shared_database: SharedDatabaseProvider,
    fx_actor: Actor,
    fx_extra_providers: Sequence[Provider],
    fx_social_clients: Sequence[BaseOAuth2[Any]] | None,
) -> AsyncIterator[FastAPI]:
    """Run the application lifespan with ``fx_actor`` signed in and ``fx_extra_providers`` applied.

    The application runs on ``fx_database`` and ``fx_books`` themselves, the catalogue storing the account of the actor,
    so a test that opens a catalogue database with ``fx_catalogue`` and the requests of the application share the write queue of
    every file. The steps run in threads
    instead of the pool of processes the lifespan would start, unless a provider of the test replaces the executor
    itself.

    :param fx_settings: Settings with a fresh data directory.
    :type fx_settings: Settings
    :param fx_shared_database: The catalogue and the books of the test, which the application is given in place of its
                               own.
    :type fx_shared_database: SharedDatabaseProvider
    :param fx_actor: Account every request acts as.
    :type fx_actor: Actor
    :param fx_extra_providers: Providers overriding the application's own.
    :type fx_extra_providers: Sequence[Provider]
    :param fx_social_clients: Social sign-in clients offered in place of the configured ones, or ``None``.
    :type fx_social_clients: Sequence[BaseOAuth2[Any]] | None
    :returns: Iterator yielding the running application and shutting it down afterwards.
    :rtype: AsyncIterator[FastAPI]
    """
    providers = [fx_shared_database, InProcessExecutorProvider(), *fx_extra_providers]
    app = create_app(fx_settings, providers, fx_social_clients)
    # The sign-in of fastapi-users stays reachable for a test that signs out, and the stub is the account of the actor
    app.state.current_user = app.dependency_overrides[signed_in_user]
    user = SignedInUser(id=fx_actor.account_id)
    app.dependency_overrides[signed_in_user] = lambda: user
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def fx_client(fx_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Open an HTTP client talking to the application in-process.

    An error that leaves the application fails the request, as it would close the connection of a real server after its
    answer, so a route that answers an expected error by a problem but lets the error go on fails its test.

    :param fx_app: The running application.
    :type fx_app: FastAPI
    :returns: Iterator yielding the client and closing it afterwards.
    :rtype: AsyncIterator[httpx.AsyncClient]
    """
    transport = httpx.ASGITransport(app=fx_app)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as client:
        yield client
