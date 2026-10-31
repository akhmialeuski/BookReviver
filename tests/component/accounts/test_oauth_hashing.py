"""Tests that a social sign-in hashes the password nobody knows in a worker thread, never on the event loop."""

import threading
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from delayed_assert import assert_expectations, expect

from tests.harness.fakes.accounts import AUTH_PATH, CSRF_HEADER, HashStep
from tests.harness.fakes.oauth import FAKE_PROVIDER_NAME, SIGN_IN_EMAIL, FakeOAuth2

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from httpx_oauth.oauth2 import BaseOAuth2

    from tests.harness.fakes.accounts import HashCalls, Visitor

pytestmark = pytest.mark.anyio

PROVIDER_PATH: str = f'{AUTH_PATH}/{FAKE_PROVIDER_NAME}'
AUTHORIZE_PATH: str = f'{PROVIDER_PATH}/authorize'
CALLBACK_PATH: str = f'{PROVIDER_PATH}/callback'
AUTHORIZATION_URL_FIELD: str = 'authorization_url'
CODE_PARAMETER: str = 'code'
STATE_PARAMETER: str = 'state'


@pytest.fixture
def fx_social_clients() -> Sequence[BaseOAuth2[Any]]:
    """Offer the fake provider in place of the configured ones.

    :returns: The fake provider alone.
    :rtype: Sequence[BaseOAuth2[Any]]
    """
    return [FakeOAuth2()]


class TestSocialSignInHashing:
    """Tests that the OAuth callback and the take-over of an account hash in a worker thread.

    The application runs in the thread of the test's event loop, so a call recorded on any other thread was made off
    the loop.
    """

    async def _sign_in(self, client: httpx.AsyncClient) -> httpx.Response:
        """Start the sign-in with the fake provider and come back with a code naming ``SIGN_IN_EMAIL``.

        :param client: Signed-out client, which keeps the state cookie of the authorization answer.
        :type client: httpx.AsyncClient
        :returns: Response of the callback route.
        :rtype: httpx.Response
        """
        authorize = await client.get(AUTHORIZE_PATH)
        state = parse_qs(urlsplit(authorize.json()[AUTHORIZATION_URL_FIELD]).query)[STATE_PARAMETER][0]
        params = {CODE_PARAMETER: SIGN_IN_EMAIL, STATE_PARAMETER: state}
        return await client.get(CALLBACK_PATH, params=params, headers={CSRF_HEADER: ''})

    async def test_first_sign_in_hashes_off_the_loop(self, fx_visitor: Visitor, fx_hash_calls: HashCalls) -> None:
        """Verify the account a first social sign-in creates gets its unknown password hashed once, in a worker thread.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_hash_calls: Record of the password helper calls of the test.
        :type fx_hash_calls: HashCalls
        """
        callback = await self._sign_in(fx_visitor.client)
        expect(callback.status_code == HTTPStatus.NO_CONTENT)
        expect(fx_hash_calls.steps == [HashStep.HASH])
        expect(threading.get_ident() not in fx_hash_calls.threads)
        assert_expectations()

    async def test_take_over_hashes_off_the_loop(self, fx_visitor: Visitor, fx_hash_calls: HashCalls) -> None:
        """Verify retiring the password of an account the provider takes over hashes the new one in a worker thread.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_hash_calls: Record of the password helper calls of the test.
        :type fx_hash_calls: HashCalls
        """
        await fx_visitor.register(SIGN_IN_EMAIL)
        fx_hash_calls.clear()
        callback = await self._sign_in(fx_visitor.client)
        expect(callback.status_code == HTTPStatus.NO_CONTENT)
        expect(fx_hash_calls.steps == [HashStep.HASH])
        expect(threading.get_ident() not in fx_hash_calls.threads)
        assert_expectations()
