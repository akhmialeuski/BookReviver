"""Fixtures shared by the whole suite."""

from typing import TYPE_CHECKING

import httpx
import pytest

from bookreviver.api.auth import current_actor
from bookreviver.app.main import create_app
from bookreviver.app.settings import AuthSettings, PersistenceBackend, Settings
from bookreviver.domain.entities import Actor
from tests.helpers.builders import new_account_id
from tests.helpers.schema import create_schema

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path
    from typing import Any

    from dishka import Provider
    from fastapi import FastAPI
    from httpx_oauth.oauth2 import BaseOAuth2

TEST_BASE_URL: str = 'http://testserver'
TEST_SECRET: str = 'test-secret-that-is-long-enough-for-signing'


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio, the loop the server uses.

    :returns: Name of the anyio backend.
    :rtype: str
    """
    return 'asyncio'


@pytest.fixture
def fx_settings(tmp_path: Path) -> Settings:
    """Build settings with in-memory persistence and a fresh data directory.

    :param tmp_path: Temporary directory of the test, holding the data directory.
    :type tmp_path: Path
    :returns: Settings with a test secret, cookies allowed over plain HTTP and no built frontend, so a build on the
              developer's machine never changes what a test sees.
    :rtype: Settings
    """
    return Settings(
        data_dir=tmp_path / 'data',
        frontend_dir=tmp_path / 'no-frontend',
        persistence=PersistenceBackend.MEMORY,
        auth=AuthSettings(secret=TEST_SECRET, cookie_secure=False),
    )


@pytest.fixture
def fx_actor() -> Actor:
    """Build the account the API tests act as.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


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
    fx_actor: Actor,
    fx_extra_providers: Sequence[Provider],
    fx_social_clients: Sequence[BaseOAuth2[Any]] | None,
) -> AsyncIterator[FastAPI]:
    """Run the application lifespan with ``fx_actor`` signed in and ``fx_extra_providers`` applied.

    The account tables live in the SQL database whatever the persistence backend, so its schema is created first.

    :param fx_settings: Settings with in-memory persistence and a fresh data directory.
    :type fx_settings: Settings
    :param fx_actor: Account every request acts as.
    :type fx_actor: Actor
    :param fx_extra_providers: Providers overriding the application's own.
    :type fx_extra_providers: Sequence[Provider]
    :param fx_social_clients: Social sign-in clients offered in place of the configured ones, or ``None``.
    :type fx_social_clients: Sequence[BaseOAuth2[Any]] | None
    :returns: Iterator yielding the running application and shutting it down afterwards.
    :rtype: AsyncIterator[FastAPI]
    """
    await create_schema(fx_settings)
    app = create_app(fx_settings, fx_extra_providers, fx_social_clients)
    app.dependency_overrides[current_actor] = lambda: fx_actor
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def fx_client(fx_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Open an HTTP client talking to the application in-process.

    :param fx_app: The running application.
    :type fx_app: FastAPI
    :returns: Iterator yielding the client and closing it afterwards.
    :rtype: AsyncIterator[httpx.AsyncClient]
    """
    transport = httpx.ASGITransport(app=fx_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as client:
        yield client
