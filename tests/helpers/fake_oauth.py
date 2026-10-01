"""A fake social sign-in provider: an httpx-oauth client that talks to no network, and the page that stands in for it.

``FakeOAuth2`` is a ``BaseOAuth2`` like the ready-made Google and Facebook clients, so the API routes, the state
cookie, the account linking and the session run exactly as they do with a real provider. Its token and profile
endpoints are answered by an ``httpx.MockTransport``. The authorization code names the address the provider vouches
for, so a test chooses who signs in by the code it sends to the callback. ``consent_route`` is the page the browser
is sent to in place of the provider's consent screen: it returns at once to the ``redirect_uri`` with a code, which
the end-to-end server mounts and the unit tests do not need.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, override
from urllib.parse import parse_qs

import httpx
from httpx_oauth.oauth2 import BaseOAuth2
from starlette.responses import RedirectResponse

if TYPE_CHECKING:
    import contextlib
    from collections.abc import Callable

    from starlette.requests import Request
    from starlette.responses import Response

FAKE_AUTHORIZE_URL: str = 'https://provider.example/authorize'
FAKE_TOKEN_URL: str = 'https://provider.example/token'
FAKE_CLIENT_ID: str = 'fake-client-id'
FAKE_CLIENT_SECRET: str = 'fake-client-secret'
FAKE_PROVIDER_NAME: str = 'google'
FAKE_DISPLAY_NAME: str = 'Google'
CONSENT_PATH: str = '/fake-provider/consent'
CODE_FIELD: str = 'code'
STATE_FIELD: str = 'state'
REDIRECT_URI_FIELD: str = 'redirect_uri'
ACCESS_TOKEN_FIELD: str = 'access_token'
ACCESS_TOKEN_PREFIX: str = 'token-for-'
ACCOUNT_ID_PREFIX: str = 'fake-account-'
SIGN_IN_EMAIL: str = 'reader@example.org'
# Authorization codes that make the fake refuse the exchange, and vouch for no address
DENIED_CODE: str = 'denied'
NO_EMAIL_CODE: str = 'no-email'


class FakeOAuth2(BaseOAuth2[dict[str, Any]]):
    """A provider whose authorization code is the email address it vouches for.

    :ivar display_name: Name the sign-in button shows, as the branded httpx-oauth clients carry.
    :ivar token_requests: Form bodies the token endpoint received, so a test can check the redirect URI it was given.
    """

    display_name: str = FAKE_DISPLAY_NAME

    def __init__(self, *, name: str = FAKE_PROVIDER_NAME, authorize_endpoint: str = FAKE_AUTHORIZE_URL) -> None:
        """Build a client with fixed credentials whose authorization endpoint is ``authorize_endpoint``.

        :param name: Name of the client, which names its routes; ``google`` by default, so the button reads as one.
        :type name: str
        :param authorize_endpoint: Address the browser is sent to for consent.
        :type authorize_endpoint: str
        """
        super().__init__(
            FAKE_CLIENT_ID, FAKE_CLIENT_SECRET, authorize_endpoint, FAKE_TOKEN_URL, name=name, base_scopes=['email']
        )
        self.token_requests: list[dict[str, str]] = []

    def _answer(self, request: httpx.Request) -> httpx.Response:
        """Answer the token exchange as a provider would.

        :param request: Request the client sends to the token endpoint.
        :type request: httpx.Request
        :returns: A token that names the address of the authorization code, or a refusal for the code ``DENIED_CODE``.
        :rtype: httpx.Response
        """
        form = {name: values[0] for name, values in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        code = form[CODE_FIELD]
        if code == DENIED_CODE:
            return httpx.Response(HTTPStatus.BAD_REQUEST, json={'error': 'invalid_grant'})
        token = {ACCESS_TOKEN_FIELD: f'{ACCESS_TOKEN_PREFIX}{code}', 'expires_in': 3600}
        return httpx.Response(HTTPStatus.OK, json=token)

    @override
    def get_httpx_client(self) -> contextlib.AbstractAsyncContextManager[httpx.AsyncClient]:
        """Return an HTTP client whose requests reach this fake instead of the network.

        :returns: Client over a mock transport answering the token endpoint.
        :rtype: contextlib.AbstractAsyncContextManager[httpx.AsyncClient]
        """
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    @override
    async def get_id_email(self, token: str) -> tuple[str, str | None]:
        """Return the identity the token was issued for, which is the address its code named.

        :param token: Access token issued by this fake.
        :type token: str
        :returns: An account identifier and the address, or no address for the code ``NO_EMAIL_CODE``.
        :rtype: tuple[str, str | None]
        """
        email = token.removeprefix(ACCESS_TOKEN_PREFIX)
        return f'{ACCOUNT_ID_PREFIX}{email}', None if email == NO_EMAIL_CODE else email


def consent_route(email: str = SIGN_IN_EMAIL) -> Callable[[Request], Response]:
    """Build the handler of the page that stands in for the provider's consent screen.

    :param email: Address the provider vouches for, which the code it returns names.
    :type email: str
    :returns: Handler that redirects to ``redirect_uri`` with the code of ``email`` and the ``state`` it was given.
    :rtype: Callable[[Request], Response]
    """

    def consent(request: Request) -> Response:
        """Send the browser back to the application as a provider does after the user agrees.

        :param request: The browser's request, carrying ``redirect_uri`` and ``state``.
        :type request: Request
        :returns: A redirect to the application.
        :rtype: Response
        """
        params = request.query_params
        query = httpx.QueryParams({CODE_FIELD: email, STATE_FIELD: params[STATE_FIELD]})
        return RedirectResponse(f'{params[REDIRECT_URI_FIELD]}?{query}')

    return consent
