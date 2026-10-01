"""Tests for CSRF protection and the rate limit on the sign-in routes."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from itsdangerous import URLSafeSerializer
from limits import parse_many

from bookreviver.app.providers.accounts import SESSION_COOKIE
from bookreviver.app.security import CSRF_FAILED, PROBLEM_MEDIA_TYPE, SIGN_IN_ATTEMPTS
from tests.helpers.fakes_accounts import (
    CSRF_COOKIE,
    CSRF_HEADER,
    DETAIL_FIELD,
    EMAIL_FIELD,
    FORGOT_PATH,
    LOGOUT_PATH,
    ME_PATH,
)

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.helpers.fakes_accounts import Visitor

pytestmark = pytest.mark.anyio

EMAIL: str = 'reader@example.org'
UNCHECKED_PATH: str = '/test/unchecked'
FORGED_TOKEN: str = 'forged'
CONTENT_TYPE: str = 'content-type'
# The secret of a server that ran before this one, and the token it signed into the browser
EARLIER_SECRET: str = 'the-secret-of-the-server-before-it-was-set-up-again'
STALE_TOKEN_VALUE: str = 'issued-before'


def _hold_token_of_another_secret(visitor: Visitor) -> None:
    """Put in the visitor's browser a CSRF token that a server with another secret signed, and send it in the header.

    :param visitor: Visitor whose browser keeps the token.
    :type visitor: Visitor
    """
    stale = URLSafeSerializer(EARLIER_SECRET, CSRF_COOKIE).dumps(STALE_TOKEN_VALUE)
    # The value of the cookie the server issued is replaced, so its domain and path are the ones a new cookie overwrites
    issued = next(cookie for cookie in visitor.client.cookies.jar if cookie.name == CSRF_COOKIE)
    visitor.client.cookies.set(CSRF_COOKIE, stale, domain=issued.domain, path=issued.path)
    visitor.client.headers[CSRF_HEADER] = stale


@pytest.fixture
def fx_unchecked_route(fx_app: FastAPI) -> None:
    """Serve a POST route at ``UNCHECKED_PATH`` that needs no session.

    :param fx_app: The running application the route is added to.
    :type fx_app: FastAPI
    """

    def accept() -> None:
        """Accept anything."""

    fx_app.add_api_route(UNCHECKED_PATH, accept, methods=['POST'])


class TestCsrfProtection:
    """Tests for the CSRF middleware install_security() adds."""

    @pytest.mark.parametrize('token', [None, FORGED_TOKEN], ids=['missing', 'forged'])
    async def test_sign_in_form_without_valid_token_is_refused(self, fx_visitor: Visitor, token: str | None) -> None:
        """Verify a signed-out form post without the matching token gets a 403 problem and no session.

        :param fx_visitor: Signed-out visitor whose client sends the CSRF header.
        :type fx_visitor: Visitor
        :param token: Value to send in the CSRF header, or ``None`` to send no header.
        :type token: str | None
        """
        if token is None:
            del fx_visitor.client.headers[CSRF_HEADER]
        else:
            fx_visitor.client.headers[CSRF_HEADER] = token
        response = await fx_visitor.login(EMAIL)
        expect(response.status_code == HTTPStatus.FORBIDDEN)
        expect(response.headers[CONTENT_TYPE].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()[DETAIL_FIELD] == CSRF_FAILED)
        expect(SESSION_COOKIE not in response.cookies)
        assert_expectations()

    async def test_signed_in_request_without_token_is_refused(self, fx_visitor: Visitor) -> None:
        """Verify a request carrying the session cookie but no token cannot act, so the session survives.

        :param fx_visitor: Signed-out visitor whose client sends the CSRF header.
        :type fx_visitor: Visitor
        """
        await fx_visitor.sign_up(EMAIL)
        del fx_visitor.client.headers[CSRF_HEADER]
        logout = await fx_visitor.client.post(LOGOUT_PATH)
        me = await fx_visitor.client.get(ME_PATH)
        expect(logout.status_code == HTTPStatus.FORBIDDEN)
        expect(me.status_code == HTTPStatus.OK)
        assert_expectations()

    async def test_cookie_signed_with_another_secret_is_replaced_by_the_next_get(self, fx_visitor: Visitor) -> None:
        """Verify a browser holding a token of an earlier secret gets a valid one from any page it loads.

        The secret changes when the server is set up again, and the browser keeps the old cookie. Without a new one
        every form fails the check, and reloading the page, which the message advises, would not help.

        :param fx_visitor: Signed-out visitor whose client sends the CSRF header.
        :type fx_visitor: Visitor
        """
        _hold_token_of_another_secret(fx_visitor)
        await fx_visitor.client.get(ME_PATH)
        fx_visitor.client.headers[CSRF_HEADER] = fx_visitor.client.cookies[CSRF_COOKIE]
        response = await fx_visitor.register(EMAIL)
        assert response.status_code == HTTPStatus.CREATED

    async def test_refusal_of_a_cookie_of_another_secret_carries_a_valid_one(self, fx_visitor: Visitor) -> None:
        """Verify the refused form already brings a valid token, so sending it again passes without a reload.

        :param fx_visitor: Signed-out visitor whose client sends the CSRF header.
        :type fx_visitor: Visitor
        """
        _hold_token_of_another_secret(fx_visitor)
        refused = await fx_visitor.register(EMAIL)
        fx_visitor.client.headers[CSRF_HEADER] = fx_visitor.client.cookies[CSRF_COOKIE]
        again = await fx_visitor.register(EMAIL)
        expect(refused.status_code == HTTPStatus.FORBIDDEN)
        expect(CSRF_COOKIE in refused.cookies)
        expect(again.status_code == HTTPStatus.CREATED)
        assert_expectations()

    async def test_a_valid_cookie_is_kept(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a browser whose token is valid is not handed a new one, which would race with forms open in tabs.

        :param fx_browser: Signed-out client holding a valid CSRF cookie.
        :type fx_browser: httpx.AsyncClient
        """
        response = await fx_browser.get(ME_PATH)
        assert CSRF_COOKIE not in response.cookies

    @pytest.mark.usefixtures('fx_unchecked_route')
    async def test_request_without_session_elsewhere_is_not_checked(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a mutating request without a session cookie passes: it cannot act for anyone.

        :param fx_client: In-process client that holds no session cookie.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.post(UNCHECKED_PATH)
        assert response.status_code == HTTPStatus.OK


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
