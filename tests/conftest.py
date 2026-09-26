"""Fixtures shared by the test suite."""

from typing import TYPE_CHECKING

import httpx
import pytest

from bookreviver.app import create_app
from bookreviver.config import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from fastapi import FastAPI

TEST_BASE_URL: str = 'http://testserver'


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only, the loop uvicorn uses."""
    return 'asyncio'


@pytest.fixture
def fx_settings(tmp_path: Path) -> Settings:
    """Build settings with a fresh data directory per test."""
    return Settings(data_dir=tmp_path / 'data')


@pytest.fixture
async def fx_app(fx_settings: Settings) -> AsyncIterator[FastAPI]:
    """Run the application lifespan, so the schema is migrated and state is set."""
    app = create_app(fx_settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def fx_client(fx_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Open an HTTP client talking to the application in-process."""
    transport = httpx.ASGITransport(app=fx_app)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as client:
        yield client
