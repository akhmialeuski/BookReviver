"""Tests for CSRF protection and the rate limit on the sign-in routes."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from limits import parse_many

from bookreviver.app.providers.accounts import SESSION_COOKIE
from bookreviver.app.security import CSRF_FAILED, PROBLEM_MEDIA_TYPE, SIGN_IN_ATTEMPTS
from tests.helpers.fakes_accounts import CSRF_HEADER, DETAIL_FIELD, EMAIL_FIELD, FORGOT_PATH, LOGOUT_PATH, ME_PATH

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.helpers.fakes_accounts import Visitor

pytestmark = pytest.mark.anyio

EMAIL: str = 'reader@example.org'
UNCHECKED_PATH: str = '/test/unchecked'
FORGED_TOKEN: str = 'forged'
CONTENT_TYPE: str = 'content-type'


@pytest.fixture
def fx_unchecked_route(fx_app: FastAPI) -> None:
    """Serve a POST route at ``UNCHECKED_PATH`` that needs no session."""

    def accept() -> None:
        """Accept anything."""

    fx_app.add_api_route(UNCHECKED_PATH, accept, methods=['POST'])


class TestCsrfProtection:
    """Tests for the CSRF middleware install_security() adds."""

    @pytest.mark.parametrize('token', [None, FORGED_TOKEN], ids=['missing', 'forged'])
    async def test_sign_in_form_without_valid_token_is_refused(self, fx_visitor: Visitor, token: str | None) -> None:
        """Verify a signed-out form post without the matching token gets a 403 problem and no session."""
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
        """Verify a request carrying the session cookie but no token cannot act, so the session survives."""
        await fx_visitor.sign_up(EMAIL)
        del fx_visitor.client.headers[CSRF_HEADER]
        logout = await fx_visitor.client.post(LOGOUT_PATH)
        me = await fx_visitor.client.get(ME_PATH)
        expect(logout.status_code == HTTPStatus.FORBIDDEN)
        expect(me.status_code == HTTPStatus.OK)
        assert_expectations()

    @pytest.mark.usefixtures('fx_unchecked_route')
    async def test_request_without_session_elsewhere_is_not_checked(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a mutating request without a session cookie passes: it cannot act for anyone."""
        response = await fx_client.post(UNCHECKED_PATH)
        assert response.status_code == HTTPStatus.OK


class TestSignInThrottle:
    """Tests for sign_in_throttle()."""

    async def test_attempts_beyond_the_limit_are_refused(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify the attempt after the per-minute limit gets a 429 problem instead of an answer."""
        allowed = parse_many(SIGN_IN_ATTEMPTS)[0].amount
        answers = [await fx_browser.post(FORGOT_PATH, json={EMAIL_FIELD: EMAIL}) for _ in range(allowed)]
        refused = await fx_browser.post(FORGOT_PATH, json={EMAIL_FIELD: EMAIL})
        expect({answer.status_code for answer in answers} == {HTTPStatus.ACCEPTED})
        expect(refused.status_code == HTTPStatus.TOO_MANY_REQUESTS)
        expect(refused.headers[CONTENT_TYPE].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()
