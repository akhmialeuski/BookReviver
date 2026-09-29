"""Tests for social sign-in: which providers are offered, and the callback of one."""

from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from delayed_assert import assert_expectations, expect
from httpx_oauth.clients.google import ACCESS_TOKEN_ENDPOINT

from bookreviver.api.schemas.accounts import AccountRead
from bookreviver.app.main import create_app
from bookreviver.app.providers.accounts import SESSION_COOKIE
from bookreviver.app.settings import OAuthClient
from tests.helpers.fakes_accounts import AUTH_PATH, CSRF_HEADER, ME_PATH

if TYPE_CHECKING:
    from bookreviver.app.settings import Settings
    from tests.helpers.fakes_accounts import RecordingMailer, Visitor

pytestmark = pytest.mark.anyio

EMAIL: str = 'reader@example.org'
PROVIDER_ACCOUNT_ID: str = 'people/1234'
ACCESS_TOKEN: str = 'provider-access-token'
AUTHORIZATION_CODE: str = 'provider-code'
CONFIGURED: OAuthClient = OAuthClient(client_id='client-id', client_secret='client-secret')
GOOGLE_HTTP_CLIENT: str = 'httpx_oauth.clients.google.GoogleOAuth2.get_httpx_client'
STATE: str = 'state'
ACCESS_TOKEN_FIELD: str = 'access_token'
AUTH_SETTINGS: str = 'auth'
GOOGLE: str = 'google'
FACEBOOK: str = 'facebook'
PROVIDERS: frozenset[str] = frozenset({GOOGLE, FACEBOOK})
AUTHORIZE_SUFFIX: str = '/authorize'


class ProviderCase(NamedTuple):
    """Providers configured in the settings and the sign-in routes the API then offers."""

    configured: frozenset[str]
    offered: frozenset[str]


def _google_api(request: httpx.Request) -> httpx.Response:
    """Answer the token exchange and the profile lookup as Google would."""
    if str(request.url) == ACCESS_TOKEN_ENDPOINT:
        return httpx.Response(HTTPStatus.OK, json={ACCESS_TOKEN_FIELD: ACCESS_TOKEN, 'expires_in': 3600})
    profile = {'resourceName': PROVIDER_ACCOUNT_ID, 'emailAddresses': [{'value': EMAIL, 'metadata': {'primary': True}}]}
    return httpx.Response(HTTPStatus.OK, json=profile)


def _google_client(_client: object) -> httpx.AsyncClient:
    """Return an HTTP client whose requests reach the fake Google instead of the network."""
    return httpx.AsyncClient(transport=httpx.MockTransport(_google_api))


def _query(url: str) -> dict[str, str]:
    """Return the query parameters of ``url``, one value each."""
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


class TestOAuthRouters:
    """Tests for the OAuth routers AccountRoutes.router() registers."""

    @pytest.mark.parametrize(
        'case',
        [
            ProviderCase(frozenset(), frozenset()),
            ProviderCase(frozenset({GOOGLE}), frozenset({GOOGLE})),
            ProviderCase(PROVIDERS, PROVIDERS),
        ],
        ids=['none', 'google', 'all'],
    )
    def test_only_configured_providers_are_offered(self, fx_settings: Settings, case: ProviderCase) -> None:
        """Verify a provider gets sign-in routes exactly when its client identifier and secret are set."""
        auth = fx_settings.auth.model_copy(update=dict.fromkeys(case.configured, CONFIGURED))
        app = create_app(fx_settings.model_copy(update={AUTH_SETTINGS: auth}))
        paths = app.openapi()['paths']
        offered = {name for name in PROVIDERS if f'{AUTH_PATH}/{name}{AUTHORIZE_SUFFIX}' in paths}
        assert offered == case.offered


class TestGoogleSignIn:
    """Tests for the Google callback, with httpx-oauth's client talking to a fake Google."""

    @pytest.fixture
    def fx_settings(self, fx_settings: Settings) -> Settings:
        """Return the suite's settings with Google sign-in configured."""
        return fx_settings.model_copy(update={AUTH_SETTINGS: fx_settings.auth.model_copy(update={GOOGLE: CONFIGURED})})

    async def _sign_in_with_google(self, client: httpx.AsyncClient) -> httpx.Response:
        """Start the sign-in, then come back to the callback as Google's redirect would, without a CSRF token."""
        authorize = await client.get(f'{AUTH_PATH}/{GOOGLE}{AUTHORIZE_SUFFIX}')
        state = _query(authorize.json()['authorization_url'])[STATE]
        params = {'code': AUTHORIZATION_CODE, STATE: state}
        return await client.get(f'{AUTH_PATH}/{GOOGLE}/callback', params=params, headers={CSRF_HEADER: ''})

    @patch(GOOGLE_HTTP_CLIENT, _google_client)
    async def test_callback_creates_verified_account_and_signs_in(
        self, fx_visitor: Visitor, fx_mailer: RecordingMailer
    ) -> None:
        """Verify a first Google sign-in creates a verified account, signs it in and sends no mail."""
        callback = await self._sign_in_with_google(fx_visitor.client)
        me = await fx_visitor.client.get(ME_PATH)
        account = AccountRead.model_validate(me.json())
        expect(callback.status_code == HTTPStatus.NO_CONTENT)
        expect(SESSION_COOKIE in callback.cookies)
        expect(account.email == EMAIL)
        expect(account.is_verified is True)
        expect(not fx_mailer.sent)
        assert_expectations()

    @patch(GOOGLE_HTTP_CLIENT, _google_client)
    async def test_callback_takes_over_unverified_account_of_the_address(self, fx_visitor: Visitor) -> None:
        """Verify Google joins the unverified account of its address, verifies it and retires its password."""
        registered = AccountRead.model_validate((await fx_visitor.register(EMAIL)).json())
        await self._sign_in_with_google(fx_visitor.client)
        account = AccountRead.model_validate((await fx_visitor.client.get(ME_PATH)).json())
        expect(account.id == registered.id)
        expect(account.is_verified is True)
        expect((await fx_visitor.login(EMAIL)).status_code == HTTPStatus.BAD_REQUEST)
        assert_expectations()
