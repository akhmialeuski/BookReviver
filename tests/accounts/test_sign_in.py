"""Tests for registration, verification, sign-in, sign-out and password reset through the HTTP API."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import pytest
from delayed_assert import assert_expectations, expect
from fastapi_users.router.common import ErrorCode

from bookreviver.api.auth import ActorDep
from bookreviver.api.schemas.accounts import AccountRead
from bookreviver.app.providers.accounts import SESSION_COOKIE, AccountMail, UserManager
from tests.helpers.fakes_accounts import (
    DETAIL_FIELD,
    EMAIL_FIELD,
    FORGOT_PATH,
    LOGOUT_PATH,
    ME_PATH,
    PASSWORD_FIELD,
    RESET_PATH,
    TOKEN_PARAMETER,
    VERIFY_PATH,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from bookreviver.app.settings import Settings
    from tests.helpers.fakes_accounts import RecordingMailer, Visitor

pytestmark = pytest.mark.anyio

EMAIL: str = 'reader@example.org'
OTHER_EMAIL: str = 'stranger@example.org'
NEW_PASSWORD: str = 'a completely different phrase'
ACTOR_PATH: str = '/test/actor'
HTTPS_BASE_URL: str = 'https://testserver'


def _session_cookie_header(response: httpx.Response) -> str:
    """Return the ``Set-Cookie`` header that carries the session.

    :param response: Response of a route that signs in.
    :type response: httpx.Response
    :returns: The header value that sets the session cookie.
    :rtype: str
    """
    return next(header for header in response.headers.get_list('set-cookie') if header.startswith(SESSION_COOKIE))


@pytest.fixture
def fx_actor_route(fx_app: FastAPI) -> None:
    """Serve ``ACTOR_PATH``, answering with the account ``current_actor`` resolves.

    :param fx_app: The running application the route is added to.
    :type fx_app: FastAPI
    """

    def whoami(actor: ActorDep) -> str:
        """Answer with the identifier of the acting account.

        :param actor: Actor ``current_actor`` resolved from the request's session.
        :type actor: ActorDep
        :returns: The account identifier as text.
        :rtype: str
        """
        return str(actor.account_id)

    fx_app.add_api_route(ACTOR_PATH, whoami)


class TestRegister:
    """Tests for POST /auth/register."""

    async def test_new_account_gets_verification_mail(self, fx_visitor: Visitor, fx_mailer: RecordingMailer) -> None:
        """Verify a registration creates an unverified account and mails a link to confirm the address.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_mailer: Recording mailer of the application.
        :type fx_mailer: RecordingMailer
        """
        response = await fx_visitor.register(EMAIL)
        account = AccountRead.model_validate(response.json())
        expect(response.status_code == HTTPStatus.CREATED)
        expect(account.email == EMAIL)
        expect(account.is_verified is False)
        expect([(message.to, message.subject) for message in fx_mailer.sent] == [(EMAIL, AccountMail.VERIFY.subject)])
        assert_expectations()

    async def test_taken_address_answers_like_a_new_one(self, fx_visitor: Visitor, fx_mailer: RecordingMailer) -> None:
        """Verify registering a taken address looks like success, mails its owner and leaves the account alone.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_mailer: Recording mailer of the application.
        :type fx_mailer: RecordingMailer
        """
        await fx_visitor.sign_up(EMAIL)
        fx_mailer.sent.clear()
        response = await fx_visitor.register(EMAIL, NEW_PASSWORD)
        expect(response.status_code == HTTPStatus.CREATED)
        expect(response.json().keys() == AccountRead.model_fields.keys())
        expect(AccountRead.model_validate(response.json()).is_verified is False)
        expect([message.subject for message in fx_mailer.sent] == [AccountMail.ALREADY_REGISTERED.subject])
        expect((await fx_visitor.login(EMAIL, NEW_PASSWORD)).status_code == HTTPStatus.BAD_REQUEST)
        expect((await fx_visitor.login(EMAIL)).status_code == HTTPStatus.NO_CONTENT)
        assert_expectations()

    @pytest.mark.parametrize(
        'password',
        ['x' * (UserManager.password_min_length - 1), f'my address is {EMAIL}'],
        ids=['too-short', 'contains-email'],
    )
    async def test_weak_password_is_refused(
        self, fx_visitor: Visitor, fx_mailer: RecordingMailer, password: str
    ) -> None:
        """Verify a password breaking a rule is refused with a reason, and no account is created.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_mailer: Recording mailer of the application.
        :type fx_mailer: RecordingMailer
        :param password: Password that breaks one of the rules.
        :type password: str
        """
        response = await fx_visitor.register(EMAIL, password)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(response.json()[DETAIL_FIELD]['code'] == ErrorCode.REGISTER_INVALID_PASSWORD)
        expect(not fx_mailer.sent)
        assert_expectations()


class TestVerify:
    """Tests for POST /auth/verify."""

    async def test_mailed_token_verifies_account(self, fx_visitor: Visitor) -> None:
        """Verify the token from the mail marks the account verified.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        await fx_visitor.register(EMAIL)
        response = await fx_visitor.verify()
        expect(response.status_code == HTTPStatus.OK)
        expect(AccountRead.model_validate(response.json()).is_verified is True)
        assert_expectations()

    async def test_forged_token_is_refused(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a token not signed by the application is refused.

        :param fx_browser: Signed-out client that passes the CSRF check.
        :type fx_browser: httpx.AsyncClient
        """
        response = await fx_browser.post(VERIFY_PATH, json={TOKEN_PARAMETER: 'not-a-token'})
        assert response.status_code == HTTPStatus.BAD_REQUEST


class TestLogin:
    """Tests for POST /auth/login."""

    async def test_unverified_account_cannot_sign_in(self, fx_visitor: Visitor) -> None:
        """Verify signing in requires a confirmed address, and no session is handed out before.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        await fx_visitor.register(EMAIL)
        response = await fx_visitor.login(EMAIL)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(response.json()[DETAIL_FIELD] == ErrorCode.LOGIN_USER_NOT_VERIFIED)
        expect(SESSION_COOKIE not in response.cookies)
        assert_expectations()

    async def test_session_cookie_is_http_only_and_lax(self, fx_visitor: Visitor, fx_settings: Settings) -> None:
        """Verify signing in sets the session in an HttpOnly, SameSite=Lax cookie living as long as configured.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_settings: Settings of the suite, holding the session lifetime.
        :type fx_settings: Settings
        """
        response = await fx_visitor.sign_up(EMAIL)
        cookie = _session_cookie_header(response)
        expect(response.status_code == HTTPStatus.NO_CONTENT)
        expect('HttpOnly' in cookie)
        expect('SameSite=lax' in cookie)
        expect(f'Max-Age={fx_settings.auth.session_lifetime_seconds}' in cookie)
        assert_expectations()

    async def test_unknown_address_and_wrong_password_look_the_same(self, fx_visitor: Visitor) -> None:
        """Verify a failed sign-in does not tell whether the address is registered.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        await fx_visitor.sign_up(EMAIL)
        await fx_visitor.client.post(LOGOUT_PATH)
        wrong_password = await fx_visitor.login(EMAIL, NEW_PASSWORD)
        unknown_address = await fx_visitor.login(OTHER_EMAIL)
        expect(wrong_password.status_code == unknown_address.status_code == HTTPStatus.BAD_REQUEST)
        expect(wrong_password.json() == unknown_address.json())
        assert_expectations()


@pytest.mark.usefixtures('fx_actor_route')
class TestCurrentActor:
    """Tests for current_actor()."""

    async def test_resolves_the_signed_in_account(self, fx_visitor: Visitor) -> None:
        """Verify ``current_actor`` resolves the account whose session the request carries.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        registered = AccountRead.model_validate((await fx_visitor.register(EMAIL)).json())
        await fx_visitor.verify()
        await fx_visitor.login(EMAIL)
        actor = await fx_visitor.client.get(ACTOR_PATH)
        me = AccountRead.model_validate((await fx_visitor.client.get(ME_PATH)).json())
        expect(actor.json() == str(registered.id))
        expect(me.email == EMAIL)
        assert_expectations()

    async def test_requires_sign_in(self, fx_browser: httpx.AsyncClient) -> None:
        """Verify a protected route refuses a request without a session.

        :param fx_browser: Signed-out client that passes the CSRF check.
        :type fx_browser: httpx.AsyncClient
        """
        response = await fx_browser.get(ACTOR_PATH)
        assert response.status_code == HTTPStatus.UNAUTHORIZED


class TestSecureCookie:
    """Tests for the Secure flag of the session cookie."""

    @pytest.fixture
    def fx_settings(self, fx_settings: Settings) -> Settings:
        """Return the suite's settings with secure cookies, as on a server behind HTTPS.

        :param fx_settings: Settings of the suite, which this fixture extends.
        :type fx_settings: Settings
        :returns: The same settings with ``cookie_secure`` on.
        :rtype: Settings
        """
        auth = fx_settings.auth.model_copy(update={'cookie_secure': True})
        return fx_settings.model_copy(update={'auth': auth})

    @pytest.fixture
    async def fx_client(self, fx_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
        """Open a client over HTTPS, since it sends Secure cookies, the CSRF one included, only there.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :returns: Iterator yielding the client and closing it afterwards.
        :rtype: AsyncIterator[httpx.AsyncClient]
        """
        transport = httpx.ASGITransport(app=fx_app)
        async with httpx.AsyncClient(transport=transport, base_url=HTTPS_BASE_URL) as client:
            yield client

    async def test_secure_setting_marks_session_cookie(self, fx_visitor: Visitor) -> None:
        """Verify ``cookie_secure`` makes the session cookie Secure.

        :param fx_visitor: Signed-out visitor over HTTPS.
        :type fx_visitor: Visitor
        """
        response = await fx_visitor.sign_up(EMAIL)
        assert 'Secure' in _session_cookie_header(response)


class TestLogout:
    """Tests for POST /auth/logout."""

    async def test_logout_revokes_the_session(self, fx_visitor: Visitor) -> None:
        """Verify signing out clears the cookie and revokes its token on the server.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        await fx_visitor.sign_up(EMAIL)
        token = fx_visitor.client.cookies[SESSION_COOKIE]
        response = await fx_visitor.client.post(LOGOUT_PATH)
        after_logout = await fx_visitor.client.get(ME_PATH)
        replayed = await fx_visitor.client.get(ME_PATH, cookies={SESSION_COOKIE: token})
        expect(response.status_code == HTTPStatus.NO_CONTENT)
        expect(after_logout.status_code == HTTPStatus.UNAUTHORIZED)
        expect(replayed.status_code == HTTPStatus.UNAUTHORIZED)
        assert_expectations()


class TestPasswordReset:
    """Tests for POST /auth/forgot-password and POST /auth/reset-password."""

    async def test_mailed_token_replaces_password(self, fx_visitor: Visitor, fx_mailer: RecordingMailer) -> None:
        """Verify the reset mail's token sets a new password and retires the old one.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        :param fx_mailer: Recording mailer of the application.
        :type fx_mailer: RecordingMailer
        """
        await fx_visitor.sign_up(EMAIL)
        await fx_visitor.client.post(LOGOUT_PATH)
        forgot = await fx_visitor.client.post(FORGOT_PATH, json={EMAIL_FIELD: EMAIL})
        token = fx_mailer.token(AccountMail.RESET.subject)
        reset = await fx_visitor.client.post(RESET_PATH, json={TOKEN_PARAMETER: token, PASSWORD_FIELD: NEW_PASSWORD})
        expect(forgot.status_code == HTTPStatus.ACCEPTED)
        expect(reset.status_code == HTTPStatus.OK)
        expect((await fx_visitor.login(EMAIL)).status_code == HTTPStatus.BAD_REQUEST)
        expect((await fx_visitor.login(EMAIL, NEW_PASSWORD)).status_code == HTTPStatus.NO_CONTENT)
        assert_expectations()

    async def test_unknown_address_answers_like_a_known_one(
        self, fx_browser: httpx.AsyncClient, fx_mailer: RecordingMailer
    ) -> None:
        """Verify asking to reset an unregistered address is accepted silently and sends nothing.

        :param fx_browser: Signed-out client that passes the CSRF check.
        :type fx_browser: httpx.AsyncClient
        :param fx_mailer: Recording mailer of the application.
        :type fx_mailer: RecordingMailer
        """
        response = await fx_browser.post(FORGOT_PATH, json={EMAIL_FIELD: OTHER_EMAIL})
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(not fx_mailer.sent)
        assert_expectations()


class TestUpdateMe:
    """Tests for PATCH /users/me."""

    async def test_password_can_be_changed(self, fx_visitor: Visitor) -> None:
        """Verify the signed-in account can set a new password.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        await fx_visitor.sign_up(EMAIL)
        response = await fx_visitor.client.patch(ME_PATH, json={PASSWORD_FIELD: NEW_PASSWORD})
        await fx_visitor.client.post(LOGOUT_PATH)
        expect(response.status_code == HTTPStatus.OK)
        expect((await fx_visitor.login(EMAIL, NEW_PASSWORD)).status_code == HTTPStatus.NO_CONTENT)
        assert_expectations()

    async def test_email_cannot_be_changed(self, fx_visitor: Visitor) -> None:
        """Verify an address change is refused before it could tell whether the new address is taken.

        :param fx_visitor: Signed-out visitor of the application.
        :type fx_visitor: Visitor
        """
        await fx_visitor.sign_up(EMAIL)
        response = await fx_visitor.client.patch(ME_PATH, json={EMAIL_FIELD: OTHER_EMAIL})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
