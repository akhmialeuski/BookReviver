"""Tests for the application factory."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect

from bookreviver.models import PAGES_TABLE, PROJECTS_TABLE

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

STYLESHEET_URL: str = '/static/style.css'


@pytest.mark.anyio
class TestCreateApp:
    """Tests for create_app()."""

    async def test_startup_migrates_schema(self, fx_app: FastAPI) -> None:
        """Verify the lifespan applies migrations, so both tables exist before the first request."""
        async with fx_app.state.session_factory() as session, session.bind.connect() as connection:
            tables = await connection.run_sync(lambda sync_connection: inspect(sync_connection).get_table_names())
        assert {PROJECTS_TABLE, PAGES_TABLE} <= set(tables)

    async def test_serves_static_files(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the stylesheet the base template links to is served."""
        response = await fx_client.get(STYLESHEET_URL)
        assert response.status_code == HTTPStatus.OK
