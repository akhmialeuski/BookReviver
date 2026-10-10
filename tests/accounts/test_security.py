"""Tests for CSRF protection and the rate limit on the sign-in routes."""

from enum import StrEnum
from http import HTTPMethod, HTTPStatus
from typing import TYPE_CHECKING

import httpx
import pytest
from attrs import frozen
from delayed_assert import assert_expectations, expect
from limits import parse_many
from starlette.responses import PlainTextResponse

from bookreviver.app.providers.accounts import SESSION_COOKIE
from bookreviver.app.security import CSRF_FAILED, PROBLEM_MEDIA_TYPE, SIGN_IN_ATTEMPTS, CSRFMiddleware
from tests.helpers.fakes_accounts import (
    CSRF_COOKIE,
    CSRF_HEADER,
    DETAIL_FIELD,
    EMAIL_FIELD,
    FORGOT_PATH,
    ME_PATH,
    REGISTER_PATH,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

EMAIL: str = 'reader@example.org'
UNCHECKED_PATH: str = '/test/unchecked'
CONTENT_TYPE: str = 'content-type'
SET_COOKIE: str = 'set-cookie'
SESSION_VALUE: str = 'any-session'
# The secret of a server that ran before this one, which signed the token a browser still holds
EARLIER_SECRET: str = 'the-secret-of-the-server-before-it-was-set-up-again'
# A byte outside ASCII, which a header can carry although the standard library compares ASCII only
NON_ASCII_VALUE: str = '\xe9'


class Token(StrEnum):
    """What a browser holds in the CSRF cookie or sends in the CSRF header."""

    ABSENT = 'absent'
    VALID = 'valid'
    OTHER_VALID = 'other-valid'
    FORGED = 'forged-signature'
    OF_EARLIER_SECRET = 'of-earlier-secret'
    NON_ASCII = 'non-ascii'


@frozen
class CsrfCase:
    """One request to the CSRF check and what the answer to it must be.

    :ivar method: Method of the request.
    :ivar path: Path of the request.
    :ivar session: Whether the request carries the session cookie.
    :ivar cookie: What the CSRF cookie of the request holds.
    :ivar header: What the CSRF header of the request holds.
    :ivar refused: Whether the check must refuse the request.
    :ivar issued: Whether the answer must set a new CSRF cookie.
    """

    method: HTTPMethod
    path: str
    session: bool
    cookie: Token
    header: Token
    refused: bool
    issued: bool


CSRF_CASES: dict[str, CsrfCase] = {
    'safe-get-passes-without-token': CsrfCase(
        HTTPMethod.GET,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.ABSENT,
        header=Token.ABSENT,
        refused=False,
        issued=True,
    ),
    'safe-head-passes-without-token': CsrfCase(
        HTTPMethod.HEAD,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.ABSENT,
        header=Token.ABSENT,
        refused=False,
        issued=True,
    ),
    'safe-options-passes-without-token': CsrfCase(
        HTTPMethod.OPTIONS,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.ABSENT,
        header=Token.ABSENT,
        refused=False,
        issued=True,
    ),
    'safe-trace-passes-without-token': CsrfCase(
        HTTPMethod.TRACE,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.ABSENT,
        header=Token.ABSENT,
        refused=False,
        issued=True,
    ),
    'post-passes-with-matching-token': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.VALID,
        refused=False,
        issued=False,
    ),
    'put-passes-with-matching-token': CsrfCase(
        HTTPMethod.PUT,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.VALID,
        refused=False,
        issued=False,
    ),
    'patch-passes-with-matching-token': CsrfCase(
        HTTPMethod.PATCH,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.VALID,
        refused=False,
        issued=False,
    ),
    'delete-passes-with-matching-token': CsrfCase(
        HTTPMethod.DELETE,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.VALID,
        refused=False,
        issued=False,
    ),
    'post-without-cookie-is-refused': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.ABSENT,
        header=Token.VALID,
        refused=True,
        issued=True,
    ),
    'post-without-header-is-refused': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.ABSENT,
        refused=True,
        issued=False,
    ),
    'delete-without-header-is-refused': CsrfCase(
        HTTPMethod.DELETE,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.ABSENT,
        refused=True,
        issued=False,
    ),
    'post-with-header-unlike-cookie-is-refused': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.OTHER_VALID,
        refused=True,
        issued=False,
    ),
    'post-with-header-outside-ascii-is-refused': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.VALID,
        header=Token.NON_ASCII,
        refused=True,
        issued=False,
    ),
    'post-with-forged-signature-is-refused-and-replaced': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.FORGED,
        header=Token.FORGED,
        refused=True,
        issued=True,
    ),
    'post-with-token-of-earlier-secret-is-refused-and-replaced': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=True,
        cookie=Token.OF_EARLIER_SECRET,
        header=Token.OF_EARLIER_SECRET,
        refused=True,
        issued=True,
    ),
    'sign-in-without-session-and-header-is-refused': CsrfCase(
        HTTPMethod.POST,
        REGISTER_PATH,
        session=False,
        cookie=Token.VALID,
        header=Token.ABSENT,
        refused=True,
        issued=False,
    ),
    'sign-in-without-session-and-cookie-is-refused': CsrfCase(
        HTTPMethod.POST,
        REGISTER_PATH,
        session=False,
        cookie=Token.ABSENT,
        header=Token.VALID,
        refused=True,
        issued=True,
    ),
    'sign-in-without-session-passes-with-matching-token': CsrfCase(
        HTTPMethod.POST,
        REGISTER_PATH,
        session=False,
        cookie=Token.VALID,
        header=Token.VALID,
        refused=False,
        issued=False,
    ),
    'post-elsewhere-without-session-passes-without-token': CsrfCase(
        HTTPMethod.POST,
        UNCHECKED_PATH,
        session=False,
        cookie=Token.ABSENT,
        header=Token.ABSENT,
        refused=False,
        issued=True,
    ),
}


def _browser_headers(cookie: str | None, header: str | None, *, session: bool) -> dict[bytes, bytes]:
    """Build the headers a browser sends: its cookies in one header, and the CSRF header when it echoes the token.

    :param cookie: Value of the CSRF cookie, or ``None`` when the browser holds none.
    :type cookie: str | None
    :param header: Value of the CSRF header, or ``None`` when the page script sends none.
    :type header: str | None
    :param session: Whether the browser holds the session cookie.
    :type session: bool
    :returns: Names and values encoded as Latin-1, as a server reads them; empty when nothing is sent.
    :rtype: dict[bytes, bytes]
    """
    cookies = [
        f'{name}={value}'
        for name, value in ((CSRF_COOKIE, cookie), (SESSION_COOKIE, SESSION_VALUE if session else None))
        if value is not None
    ]
    headers = {CSRF_HEADER: header} if header is not None else {}
    if cookies:
        headers['cookie'] = '; '.join(cookies)
    return {name.encode('latin-1'): value.encode('latin-1') for name, value in headers.items()}


@pytest.fixture
def fx_unchecked_route(fx_app: FastAPI) -> None:
    """Serve a route at ``UNCHECKED_PATH`` that needs no session and answers every method the checks tell apart.

    :param fx_app: The running application the route is added to.
    :type fx_app: FastAPI
    """

    def accept() -> None:
        """Accept anything."""

    fx_app.add_api_route(UNCHECKED_PATH, accept, methods=list(HTTPMethod))


@pytest.fixture
def fx_tokens(fx_app: FastAPI, fx_settings: Settings) -> dict[Token, str]:
    """Issue the tokens a browser can hold, signed by the application and by a server that ran before it.

    :param fx_app: The running application, which the middleware under test is built over.
    :type fx_app: FastAPI
    :param fx_settings: Settings holding the secret of the application.
    :type fx_settings: Settings
    :returns: A value for every ``Token`` but ``ABSENT``.
    :rtype: dict[Token, str]
    """
    server = CSRFMiddleware(
        fx_app, secret=fx_settings.auth.secret.get_secret_value(), sign_in_paths=frozenset(), secure=False
    )
    earlier = CSRFMiddleware(fx_app, secret=EARLIER_SECRET, sign_in_paths=frozenset(), secure=False)
    valid = server.new_token()
    return {
        Token.VALID: valid,
        Token.OTHER_VALID: server.new_token(),
        # Not a character of the signature, so the signature surely differs
        Token.FORGED: f'{valid[:-1]}x',
        Token.OF_EARLIER_SECRET: earlier.new_token(),
        Token.NON_ASCII: NON_ASCII_VALUE,
    }


class TestCsrfProtection:
    """Tests for the CSRF middleware install_security() adds."""

    @pytest.mark.usefixtures('fx_unchecked_route')
    @pytest.mark.parametrize('case', CSRF_CASES.values(), ids=list(CSRF_CASES))
    async def test_request_is_checked_against_cookie_and_header(
        self, fx_client: httpx.AsyncClient, fx_tokens: dict[Token, str], case: CsrfCase
    ) -> None:
        """Verify what the check refuses, what it lets through, and which answers bring a new token.

        A refusal is a 403 problem document. A token that the answer brings is one the next request passes with.

        :param fx_client: In-process client that holds no cookie of its own.
        :type fx_client: httpx.AsyncClient
        :param fx_tokens: The tokens a browser can hold.
        :type fx_tokens: dict[Token, str]
        :param case: The request and the expected answer.
        :type case: CsrfCase
        """
        headers = _browser_headers(fx_tokens.get(case.cookie), fx_tokens.get(case.header), session=case.session)
        response = await fx_client.request(case.method, case.path, headers=headers)
        expect((response.status_code == HTTPStatus.FORBIDDEN) == case.refused)
        expect((CSRF_COOKIE in response.cookies) == case.issued)
        if case.refused:
            expect(response.headers[CONTENT_TYPE].startswith(PROBLEM_MEDIA_TYPE))
            expect(response.json()[DETAIL_FIELD] == CSRF_FAILED)
        if case.issued:
            new = response.cookies[CSRF_COOKIE]
            again = await fx_client.request(
                case.method, case.path, headers=_browser_headers(new, new, session=case.session)
            )
            expect(again.status_code != HTTPStatus.FORBIDDEN)
        assert_expectations()

    @pytest.mark.parametrize('secure', [False, True], ids=['plain-http', 'https'])
    async def test_new_cookie_carries_the_configured_attributes(self, *, secure: bool) -> None:
        """Verify the cookie is sent on top-level navigations, readable by the page script, and secure on demand.

        :param secure: Whether the middleware is set to send the cookie over HTTPS only.
        :type secure: bool
        """
        app = CSRFMiddleware(PlainTextResponse(''), secret=EARLIER_SECRET, sign_in_paths=frozenset(), secure=secure)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            response = await client.get('/')
        attributes = set(response.headers[SET_COOKIE].split('; '))
        expect(response.headers[SET_COOKIE].startswith(f'{CSRF_COOKIE}='))
        expect({'Path=/', 'SameSite=lax'} <= attributes)
        expect('HttpOnly' not in attributes)
        expect(('Secure' in attributes) == secure)
        assert_expectations()

    async def test_a_valid_cookie_is_kept_by_a_get(
        self, fx_client: httpx.AsyncClient, fx_tokens: dict[Token, str]
    ) -> None:
        """Verify a browser whose token is valid is not handed a new one, which would race with forms open in tabs.

        :param fx_client: In-process client that holds no cookie of its own.
        :type fx_client: httpx.AsyncClient
        :param fx_tokens: The tokens a browser can hold.
        :type fx_tokens: dict[Token, str]
        """
        valid = fx_tokens[Token.VALID]
        response = await fx_client.get(ME_PATH, headers=_browser_headers(valid, None, session=False))
        assert CSRF_COOKIE not in response.cookies


class TestSignInThrottle:
    """Tests for sign_in_throttle()."""

    async def test_attempts_beyond_the_limit_are_refused(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify the attempt after the per-minute limit gets a 429 problem instead of an answer.

        :param fx_browser: Signed-out client that passes the CSRF check.
        :type fx_browser: httpx.AsyncClient
        """
        allowed = parse_many(SIGN_IN_ATTEMPTS)[0].amount
        answers = [await fx_browser.post(FORGOT_PATH, json={EMAIL_FIELD: EMAIL}) for _ in range(allowed)]
        refused = await fx_browser.post(FORGOT_PATH, json={EMAIL_FIELD: EMAIL})
        expect({answer.status_code for answer in answers} == {HTTPStatus.ACCEPTED})
        expect(refused.status_code == HTTPStatus.TOO_MANY_REQUESTS)
        expect(refused.headers[CONTENT_TYPE].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()
