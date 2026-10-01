"""Tests for the application factory: what ``create_app`` serves besides the API."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from fastapi import FastAPI

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

INDEX_HTML: str = '<!doctype html><title>BookReviver</title><div id="root"></div>'
APP_JS: str = 'console.log("book");'
HTML_ACCEPT: dict[str, str] = {'accept': 'text/html,application/xhtml+xml'}
JSON_ACCEPT: dict[str, str] = {'accept': 'application/json'}


class TestCreateAppWithFrontend:
    """Tests for an application whose ``frontend_dir`` holds a build."""

    @pytest.fixture
    def fx_frontend_dir(self, tmp_path: Path) -> Path:
        """Write a small built frontend: an ``index.html`` and one asset.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :returns: Directory holding the build.
        :rtype: Path
        """
        directory = tmp_path / 'dist'
        (directory / 'assets').mkdir(parents=True)
        (directory / 'index.html').write_text(INDEX_HTML)
        (directory / 'assets' / 'app.js').write_text(APP_JS)
        return directory

    @pytest.fixture
    def fx_settings(self, fx_settings: Settings, fx_frontend_dir: Path) -> Settings:
        """Point the settings of the suite at the written build.

        :param fx_settings: Settings of the suite, without a frontend.
        :type fx_settings: Settings
        :param fx_frontend_dir: Directory holding the build.
        :type fx_frontend_dir: Path
        :returns: The same settings with ``frontend_dir`` set.
        :rtype: Settings
        """
        return fx_settings.model_copy(update={'frontend_dir': fx_frontend_dir})

    async def test_root_serves_the_index_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify ``/`` answers with the ``index.html`` of the build.

        :param fx_client: Client talking to the application with a build in place.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get('/', headers=HTML_ACCEPT)

        assert response.status_code == HTTPStatus.OK
        assert response.text == INDEX_HTML
        assert response.headers['content-type'].startswith('text/html')

    async def test_assets_are_served_as_files(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a file of the build is served at its path.

        :param fx_client: Client talking to the application with a build in place.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get('/assets/app.js')

        assert response.status_code == HTTPStatus.OK
        assert response.text == APP_JS

    async def test_client_side_route_falls_back_to_the_index_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a browser that opens or reloads ``/projects/<id>`` gets the page the router then resolves.

        :param fx_client: Client talking to the application with a build in place.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get('/projects/0d4cf2a6', headers=HTML_ACCEPT)

        assert response.status_code == HTTPStatus.OK
        assert response.text == INDEX_HTML

    async def test_missing_asset_is_not_found_rather_than_the_index_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a request that does not ask for a page gets a 404 for a file the build does not hold.

        :param fx_client: Client talking to the application with a build in place.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get('/assets/missing.js', headers={'accept': '*/*'})

        assert response.status_code == HTTPStatus.NOT_FOUND

    async def test_api_routes_win_over_the_frontend(self, fx_client: httpx.AsyncClient) -> None:
        """Verify an address of the API is answered by its route, never by the page.

        :param fx_client: Client talking to the application with a build in place.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get('/api/v1/projects', headers=HTML_ACCEPT)

        assert response.status_code == HTTPStatus.OK
        assert response.headers['content-type'] == 'application/json'

    async def test_unknown_api_address_is_not_answered_with_the_page_for_a_client_of_the_api(
        self, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify a client that asks for JSON gets a 404 for an unknown address under ``/api``, not ``index.html``.

        :param fx_client: Client talking to the application with a build in place.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get('/api/v1/no-such-route', headers=JSON_ACCEPT)

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert INDEX_HTML not in response.text

    async def test_frontend_is_not_part_of_the_published_schema(self, fx_app: FastAPI) -> None:
        """Verify the pages of the build do not appear in the OpenAPI schema the client is generated from.

        :param fx_app: The running application with a build in place.
        :type fx_app: FastAPI
        """
        paths = fx_app.openapi()['paths']

        assert '/' not in paths
        assert all(path.startswith('/api/') for path in paths)


class TestCreateAppWithoutFrontend:
    """Tests for an application whose ``frontend_dir`` does not exist."""

    async def test_api_is_served_and_the_root_is_not_found(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the application starts without a build, serves the API and answers 404 for the root.

        :param fx_client: Client talking to the application without a build.
        :type fx_client: httpx.AsyncClient
        """
        api = await fx_client.get('/api/v1/projects')
        root = await fx_client.get('/', headers=HTML_ACCEPT)

        assert api.status_code == HTTPStatus.OK
        assert root.status_code == HTTPStatus.NOT_FOUND
