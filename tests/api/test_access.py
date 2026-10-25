"""Tests for the guard that refuses every route of the API without a session, apart from the public ones."""

import re
from http import HTTPMethod
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.auth import signed_in_user
from bookreviver.api.routers.iiif import IIIF_PREFIX
from bookreviver.api.routing import API_PREFIX
from tests.helpers.builders import new_book_id

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

pytestmark = pytest.mark.anyio

# The routes of sign-in and of the signed-in account, and the files anyone holding their address reads
PUBLIC_PREFIXES: tuple[str, ...] = (f'{API_PREFIX}/auth', f'{API_PREFIX}/users', f'{API_PREFIX}{IIIF_PREFIX}')
PROVIDERS_PATH: str = f'{API_PREFIX}/auth/providers'
PATH_PARAMETER: re.Pattern[str] = re.compile(r'\{[^}]+\}')


@pytest.fixture
def fx_signed_out(fx_app: FastAPI) -> None:
    """Take the test's sign-in off, so a request has no session.

    :param fx_app: The running application, whose stub user is removed.
    :type fx_app: FastAPI
    """
    fx_app.dependency_overrides.pop(signed_in_user)


@pytest.mark.usefixtures('fx_signed_out')
class TestGuard:
    """Tests for the dependency that every route of the API but the public ones resolves."""

    async def test_every_route_but_the_public_ones_refuses_a_caller_without_a_session(
        self, fx_app: FastAPI, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify every operation of the API answers 401 with no session, and no body or path is looked at first.

        The operations are those the application publishes, so a route that forgot its dependency is found without a
        list kept by hand. Each public group must have a route, so the check cannot pass on an application that
        mounts none of them.

        :param fx_app: The running application with every router included.
        :type fx_app: FastAPI
        :param fx_client: Client without a session.
        :type fx_client: httpx.AsyncClient
        """
        operations = [
            (HTTPMethod(method.upper()), PATH_PARAMETER.sub(str(new_book_id()), path))
            for path, item in fx_app.openapi()['paths'].items()
            if path.startswith(API_PREFIX)
            for method in item
        ]
        guarded = [(method, path) for method, path in operations if not path.startswith(PUBLIC_PREFIXES)]

        refused = [
            (method, path)
            for method, path in guarded
            if (await fx_client.request(method, path)).status_code == status.HTTP_401_UNAUTHORIZED
        ]

        expect(len(guarded) > 0)
        expect([operation for operation in guarded if operation not in refused] == [])
        for prefix in PUBLIC_PREFIXES:
            expect(any(path.startswith(prefix) for _method, path in operations), f'no route below {prefix}')
        assert_expectations()

    async def test_public_routes_answer_without_a_session(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the list of sign-in providers and a file of a book are not behind the guard.

        :param fx_client: Client without a session.
        :type fx_client: httpx.AsyncClient
        """
        providers = await fx_client.get(PROVIDERS_PATH)
        missing_file = await fx_client.get(f'{API_PREFIX}{IIIF_PREFIX}/{new_book_id()}/{new_book_id()}')

        expect(providers.status_code == status.HTTP_200_OK)
        expect(missing_file.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()
