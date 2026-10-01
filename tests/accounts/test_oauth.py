"""Tests for social sign-in: which providers are offered, and the callback of one."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from delayed_assert import assert_expectations, expect
from fastapi_users.router.common import ErrorCode
from httpx_oauth.clients.google import ACCESS_TOKEN_ENDPOINT

from bookreviver.api.schemas.accounts import AccountRead
from bookreviver.app.main import create_app
from bookreviver.app.providers.accounts import SESSION_COOKIE
from bookreviver.app.settings import OAuthClient
from tests.helpers.fake_oauth import FAKE_CLIENT_ID, NO_EMAIL_CODE, SIGN_IN_EMAIL, FakeOAuth2
from tests.helpers.fakes_accounts import AUTH_PATH, CSRF_HEADER, ME_PATH

if TYPE_CHECKING:
    from collections.abc import Sequence

    from httpx_oauth.oauth2 import BaseOAuth2

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
PROVIDERS_PATH: str = f'{AUTH_PATH}/providers'
LABELS: dict[str, str] = {GOOGLE: 'Google', FACEBOOK: 'Facebook'}
PUBLIC_URL: str = 'http://127.0.0.1:8000'
OAUTH_CSRF_COOKIE: str = 'fastapiusersoauthcsrf'


class ProviderCase(NamedTuple):
    """Providers configured in the settings and the sign-in routes the API then offers.

    :ivar configured: Names of the providers whose credentials are set.
    :ivar offered: Names of the providers that get sign-in routes.
    """

    configured: frozenset[str]
    offered: frozenset[str]


def _google_api(request: httpx.Request) -> httpx.Response:
    """Answer the token exchange and the profile lookup as Google would.

    :param request: Request the OAuth client sends to Google.
    :type request: httpx.Request
    :returns: A token for the token endpoint, a profile for anything else.
    :rtype: httpx.Response
    """
    if str(request.url) == ACCESS_TOKEN_ENDPOINT:
        return httpx.Response(HTTPStatus.OK, json={ACCESS_TOKEN_FIELD: ACCESS_TOKEN, 'expires_in': 3600})
    profile = {'resourceName': PROVIDER_ACCOUNT_ID, 'emailAddresses': [{'value': EMAIL, 'metadata': {'primary': True}}]}
    return httpx.Response(HTTPStatus.OK, json=profile)


def _google_client(_client: object) -> httpx.AsyncClient:
    """Return an HTTP client whose requests reach the fake Google instead of the network.

    :param _client: The OAuth client this replaces ``get_httpx_client`` of, unused.
    :type _client: object
    :returns: Client over a mock transport answering as Google.
    :rtype: httpx.AsyncClient
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(_google_api))


def _query(url: str) -> dict[str, str]:
    """Return the query parameters of ``url``, one value each.

    :param url: URL to read.
    :type url: str
    :returns: Query parameter names with their first values.
    :rtype: dict[str, str]
    """
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
        """Verify a provider gets sign-in routes exactly when its client identifier and secret are set.

        :param fx_settings: Settings of the suite.
        :type fx_settings: Settings
        :param case: Providers to configure and the ones expected to be offered.
        :type case: ProviderCase
        """
        auth = fx_settings.auth.model_copy(update=dict.fromkeys(case.configured, CONFIGURED))
        app = create_app(fx_settings.model_copy(update={AUTH_SETTINGS: auth}))
        paths = app.openapi()['paths']
        offered = {name for name in PROVIDERS if f'{AUTH_PATH}/{name}{AUTHORIZE_SUFFIX}' in paths}
        assert offered == case.offered


class TestGoogleSignIn:
    """Tests for the Google callback, with httpx-oauth's client talking to a fake Google."""

    @pytest.fixture
    def fx_settings(self, fx_settings: Settings) -> Settings:
        """Return the suite's settings with Google sign-in configured.

        :param fx_settings: Settings of the suite, which this fixture extends.
        :type fx_settings: Settings
        :returns: The same settings with Google credentials set.
        :rtype: Settings
        """
        return fx_settings.model_copy(update={AUTH_SETTINGS: fx_settings.auth.model_copy(update={GOOGLE: CONFIGURED})})

    async def _sign_in_with_google(self, client: httpx.AsyncClient) -> httpx.Response:
        """Start the sign-in, then come back to the callback as Google's redirect would, without a CSRF token.

        :param client: Signed-out client of the application.
        :type client: httpx.AsyncClient
        :returns: Response of the callback route.
        :rtype: httpx.Response
        """
        authorize = await client.get(f'{AUTH_PATH}/{GOOGLE}{AUTHORIZE_SUFFIX}')
        state = _query(authorize.json()['authorization_url'])[STATE]
        params = {'code': AUTHORIZATION_CODE, STATE: state}
        return await client.get(f'{AUTH_PATH}/{GOOGLE}/callback', params=params, headers={CSRF_HEADER: ''})

    @patch(GOOGLE_HTTP_CLIENT, _google_client)
    async def test_callback_creates_verified_account_and_signs_in(
        self, fx_visitor: Visitor, fx_mailer: RecordingMailer
    ) -> None:
        """Verify a first Google sign-in creates a verified account, signs it in and sends no mail.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_mailer: Recording mailer of the application.
        :type fx_mailer: RecordingMailer
        """
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
        """Verify Google joins the unverified account of its address, verifies it and retires its password.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        registered = AccountRead.model_validate((await fx_visitor.register(EMAIL)).json())
        await self._sign_in_with_google(fx_visitor.client)
        account = AccountRead.model_validate((await fx_visitor.client.get(ME_PATH)).json())
        expect(account.id == registered.id)
        expect(account.is_verified is True)
        expect((await fx_visitor.login(EMAIL)).status_code == HTTPStatus.BAD_REQUEST)
        assert_expectations()


class TestListProviders:
    """Tests for ``GET /auth/providers``, the public list the sign-in screen builds its buttons from."""

    @pytest.mark.parametrize(
        'case',
        [
            ProviderCase(frozenset(), frozenset()),
            ProviderCase(frozenset({GOOGLE}), frozenset({GOOGLE})),
            ProviderCase(PROVIDERS, PROVIDERS),
        ],
        ids=['none', 'google', 'all'],
    )
    async def test_lists_exactly_the_configured_providers(self, fx_settings: Settings, case: ProviderCase) -> None:
        """Verify the list holds a name and a label for each provider with credentials, and nothing for the others.

        :param fx_settings: Settings of the suite.
        :type fx_settings: Settings
        :param case: Providers to configure and the ones expected in the list.
        :type case: ProviderCase
        """
        auth = fx_settings.auth.model_copy(update=dict.fromkeys(case.configured, CONFIGURED))
        app = create_app(fx_settings.model_copy(update={AUTH_SETTINGS: auth}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.get(PROVIDERS_PATH)
        listed = {provider['name']: provider['label'] for provider in response.json()}
        expect(response.status_code == HTTPStatus.OK)
        expect(listed == {name: LABELS[name] for name in case.offered})
        # The credentials never leave the server
        expect(CONFIGURED.client_id not in response.text)
        expect(CONFIGURED.client_secret.get_secret_value() not in response.text)
        assert_expectations()

    async def test_is_public(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a visitor who is not signed in may read the list.

        :param fx_browser: Signed-out client of the application.
        :type fx_browser: httpx.AsyncClient
        """
        response = await fx_browser.get(PROVIDERS_PATH)
        assert response.status_code == HTTPStatus.OK


class TestFakeProviderSignIn:
    """Tests for the whole path from ``authorize`` to a session, over a fake provider and the frontend callback page."""

    @pytest.fixture
    def fx_provider(self) -> FakeOAuth2:
        """Build the fake provider of one test.

        :returns: A fake client named ``google`` that records the token requests it receives.
        :rtype: FakeOAuth2
        """
        return FakeOAuth2()

    @pytest.fixture
    def fx_social_clients(self, fx_provider: FakeOAuth2) -> Sequence[BaseOAuth2[Any]]:
        """Offer the fake provider in place of the configured ones.

        :param fx_provider: Fake provider of the test.
        :type fx_provider: FakeOAuth2
        :returns: The fake provider alone.
        :rtype: Sequence[BaseOAuth2[Any]]
        """
        return [fx_provider]

    async def _authorize(self, client: httpx.AsyncClient) -> str:
        """Ask for the provider's address, as the sign-in screen does, and return the ``state`` it carries.

        :param client: Signed-out client, which keeps the state cookie of the response.
        :type client: httpx.AsyncClient
        :returns: The ``state`` parameter of the authorization URL.
        :rtype: str
        """
        authorize = await client.get(f'{AUTH_PATH}/{GOOGLE}{AUTHORIZE_SUFFIX}')
        return _query(authorize.json()['authorization_url'])[STATE]

    async def test_authorization_url_returns_to_the_frontend_page(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify the provider is told to return to ``{public_url}/auth/google/callback`` of the web interface.

        :param fx_browser: Signed-out client of the application.
        :type fx_browser: httpx.AsyncClient
        """
        authorize = await fx_browser.get(f'{AUTH_PATH}/{GOOGLE}{AUTHORIZE_SUFFIX}')
        query = _query(authorize.json()['authorization_url'])
        expect(query['redirect_uri'] == f'{PUBLIC_URL}/auth/{GOOGLE}/callback')
        expect(query['client_id'] == FAKE_CLIENT_ID)
        expect('fastapiusersoauthcsrf' in authorize.cookies)
        assert_expectations()

    async def test_page_forwards_code_and_state_and_the_browser_is_signed_in(
        self, fx_browser: httpx.AsyncClient, fx_provider: FakeOAuth2
    ) -> None:
        """Verify the callback, called as the frontend page does, signs the browser in and exchanges the page's URI.

        :param fx_browser: Signed-out client of the application, which keeps the state cookie the way a browser does.
        :type fx_browser: httpx.AsyncClient
        :param fx_provider: Fake provider of the application.
        :type fx_provider: FakeOAuth2
        """
        state = await self._authorize(fx_browser)
        callback = await fx_browser.get(
            f'{AUTH_PATH}/{GOOGLE}/callback', params={'code': SIGN_IN_EMAIL, STATE: state}, headers={CSRF_HEADER: ''}
        )
        me = await fx_browser.get(ME_PATH)
        expect(callback.status_code == HTTPStatus.NO_CONTENT)
        expect(SESSION_COOKIE in callback.cookies)
        expect(me.json()['email'] == SIGN_IN_EMAIL)
        expect(me.json()['is_verified'] is True)
        # The provider checks the redirect URI of the exchange against the one of the authorization request
        expect(
            [request['redirect_uri'] for request in fx_provider.token_requests]
            == [f'{PUBLIC_URL}/auth/{GOOGLE}/callback']
        )
        assert_expectations()

    async def test_state_from_another_browser_is_refused(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a callback whose state cookie is missing, as in a browser that did not start the sign-in, is a 400.

        :param fx_browser: Signed-out client of the application.
        :type fx_browser: httpx.AsyncClient
        """
        state = await self._authorize(fx_browser)
        fx_browser.cookies.delete(OAUTH_CSRF_COOKIE)
        callback = await fx_browser.get(
            f'{AUTH_PATH}/{GOOGLE}/callback', params={'code': SIGN_IN_EMAIL, STATE: state}, headers={CSRF_HEADER: ''}
        )
        expect(callback.status_code == HTTPStatus.BAD_REQUEST)
        expect(callback.json()['detail'] == ErrorCode.OAUTH_INVALID_STATE)
        expect(SESSION_COOKIE not in callback.cookies)
        assert_expectations()

    async def test_forged_state_is_refused(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a state that this server did not sign is a 400, and signs nobody in.

        :param fx_browser: Signed-out client of the application.
        :type fx_browser: httpx.AsyncClient
        """
        callback = await fx_browser.get(
            f'{AUTH_PATH}/{GOOGLE}/callback', params={'code': SIGN_IN_EMAIL, STATE: 'forged'}, headers={CSRF_HEADER: ''}
        )
        expect(callback.status_code == HTTPStatus.BAD_REQUEST)
        expect(callback.json()['detail'] == ErrorCode.ACCESS_TOKEN_DECODE_ERROR)
        assert_expectations()

    async def test_provider_without_an_address_is_refused(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a provider that vouches for no address cannot sign anyone in.

        :param fx_browser: Signed-out client of the application.
        :type fx_browser: httpx.AsyncClient
        """
        state = await self._authorize(fx_browser)
        callback = await fx_browser.get(
            f'{AUTH_PATH}/{GOOGLE}/callback', params={'code': NO_EMAIL_CODE, STATE: state}, headers={CSRF_HEADER: ''}
        )
        expect(callback.status_code == HTTPStatus.BAD_REQUEST)
        expect(callback.json()['detail'] == ErrorCode.OAUTH_NOT_AVAILABLE_EMAIL)
        assert_expectations()

    async def test_provider_error_is_a_400(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a callback that carries the provider's ``error`` instead of a code, as when the user declines, is a 400.

        :param fx_browser: Signed-out client of the application.
        :type fx_browser: httpx.AsyncClient
        """
        state = await self._authorize(fx_browser)
        callback = await fx_browser.get(
            f'{AUTH_PATH}/{GOOGLE}/callback', params={'error': 'access_denied', STATE: state}, headers={CSRF_HEADER: ''}
        )
        expect(callback.status_code == HTTPStatus.BAD_REQUEST)
        expect(SESSION_COOKIE not in callback.cookies)
        assert_expectations()
