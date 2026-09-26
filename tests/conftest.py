"""Fixtures shared by the whole suite."""

from typing import TYPE_CHECKING

import httpx
import pytest

from bookreviver.api.auth import current_actor
from bookreviver.app.main import create_app
from bookreviver.app.settings import AuthSettings, PersistenceBackend, Settings
from bookreviver.domain.entities import Actor
from tests.helpers.builders import new_account_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

TEST_BASE_URL: str = 'http://testserver'
TEST_SECRET: str = 'test-secret-that-is-long-enough-for-signing'


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio, the loop the server uses."""
    return 'asyncio'


@pytest.fixture
def fx_settings(tmp_path: Path) -> Settings:
    """Build settings with in-memory persistence and a fresh data directory."""
    return Settings(
        data_dir=tmp_path / 'data',
        persistence=PersistenceBackend.MEMORY,
        auth=AuthSettings(secret=TEST_SECRET, cookie_secure=False),
    )


@pytest.fixture
def fx_actor() -> Actor:
    """Build the account the API tests act as."""
    return Actor(account_id=new_account_id())


@pytest.fixture
async def fx_app(fx_settings: Settings, fx_actor: Actor) -> AsyncIterator[FastAPI]:
    """Run the application lifespan with ``fx_actor`` signed in."""
    app = create_app(fx_settings)
    app.dependency_overrides[current_actor] = lambda: fx_actor
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def fx_client(fx_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Open an HTTP client talking to the application in-process."""
    transport = httpx.ASGITransport(app=fx_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as client:
        yield client
