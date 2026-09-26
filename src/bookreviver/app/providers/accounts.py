"""Provider of the accounts feature: users, sessions, OAuth clients and mail, all on fastapi-users."""

import enum
import hashlib
import hmac
from base64 import urlsafe_b64encode
from typing import TYPE_CHECKING, Any, Literal, override
from urllib.parse import urlencode
from uuid import UUID, uuid4

from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import inject
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from httpx_oauth.clients.facebook import FacebookOAuth2
from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.exceptions import GetIdEmailError
from httpx_oauth.oauth2 import BaseOAuth2
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.adapters.mail.log import LogMailer
from bookreviver.adapters.mail.smtp import SmtpMailer, SmtpServer
from bookreviver.adapters.persistence.sqlalchemy.accounts import (
    AccessTokenDatabase,
    AccessTokenTable,
    AccountDatabase,
    AccountTable,
)
from bookreviver.api.routers.accounts import AccountRoutes
from bookreviver.app.settings import Settings
from bookreviver.domain.values import MailMessage
from bookreviver.ports.runtime import Mailer

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import Request, Response, params
    from fastapi_users.schemas import BaseUserCreate
    from httpx_oauth.oauth2 import OAuth2ClientAuthMethod, OAuth2Token

    from bookreviver.app.settings import AuthSettings, OAuthClient

SessionStrategy = DatabaseStrategy[AccountTable, UUID, AccessTokenTable]

SESSION_COOKIE: str = 'bookreviver_session'
AUTH_BACKEND: str = 'cookie'
PASSWORD_TOO_SHORT: str = 'Use a password of at least {length} characters.'
PASSWORD_CONTAINS_EMAIL: str = 'The password must not contain the email address.'

X_AUTHORIZE_ENDPOINT: str = 'https://x.com/i/oauth2/authorize'
X_TOKEN_ENDPOINT: str = 'https://api.x.com/2/oauth2/token'
X_REVOKE_ENDPOINT: str = 'https://api.x.com/2/oauth2/revoke'
X_PROFILE_ENDPOINT: str = 'https://api.x.com/2/users/me'
# users.email lets X return the confirmed address, and X grants users.read only together with tweet.read
X_SCOPES: list[str] = ['users.read', 'tweet.read', 'users.email']
X_EMAIL_FIELD: str = 'confirmed_email'
# X authenticates a confidential client at its token and revocation endpoints with HTTP Basic
X_CLIENT_AUTH: OAuth2ClientAuthMethod = 'client_secret_basic'
PKCE_METHOD: Literal['S256'] = 'S256'
PKCE_CONTEXT: bytes = b'bookreviver:x:pkce-verifier'


class AccountMail(enum.Enum):
    """Messages about an account, each linking to a page of the web interface."""

    VERIFY = (
        'verify-email',
        'Confirm your email address',
        'Open this link to confirm the email address of your BookReviver account:\n\n{link}\n',
    )
    RESET = (
        'reset-password',
        'Reset your password',
        (
            'Open this link to choose a new password for your BookReviver account:\n\n{link}\n\n'
            'If you did not ask for this, ignore the message and your password stays as it is.\n'
        ),
    )
    ALREADY_REGISTERED = (
        'sign-in',
        'Your BookReviver account',
        (
            'Someone tried to register a BookReviver account with this address, which already has one.\n'
            'If it was you, sign in, or reset your password from the sign-in page:\n\n{link}\n'
        ),
    )

    page: str
    subject: str
    body: str

    def __init__(self, page: str, subject: str, body: str) -> None:
        self.page = page
        self.subject = subject
        self.body = body

    def to(self, address: str, *, public_url: str, token: str = '') -> MailMessage:
        """Build the message for ``address``, its link carrying ``token`` when one is given."""
        query = f'?{urlencode({"token": token})}' if token else ''
        link = f'{public_url.rstrip("/")}/{self.page}{query}'
        return MailMessage(to=address, subject=self.subject, body=self.body.format(link=link))


class UserManager(UUIDIDMixin, BaseUserManager[AccountTable, UUID]):
    """fastapi-users' account rules, with mail sent through the ``Mailer`` port.

    No answer reveals whether an address is registered: registering a taken address answers like a new
    registration and mails the owner instead.
    """

    password_min_length: int = 12

    def __init__(self, user_db: AccountDatabase, *, mailer: Mailer, settings: Settings) -> None:
        super().__init__(user_db)
        secret = settings.auth.secret.get_secret_value()
        self.verification_token_secret = secret
        self.reset_password_token_secret = secret
        self._mailer = mailer
        self._public_url = settings.public_url

    @override
    async def create(
        self, user_create: BaseUserCreate, safe: bool = False, request: Request | None = None
    ) -> AccountTable:
        try:
            return await super().create(user_create, safe, request)
        except exceptions.UserAlreadyExists:
            pass
        # Spend the time a new account costs, and answer with an account that is never stored
        self.password_helper.hash(user_create.password)
        await self._mailer.send(AccountMail.ALREADY_REGISTERED.to(user_create.email, public_url=self._public_url))
        return AccountTable(id=uuid4(), email=user_create.email, is_active=True, is_superuser=False, is_verified=False)

    @override
    async def validate_password(self, password: str, user: BaseUserCreate | AccountTable) -> None:
        if len(password) < self.password_min_length:
            raise exceptions.InvalidPasswordException(reason=PASSWORD_TOO_SHORT.format(length=self.password_min_length))
        if user.email.lower() in password.lower():
            raise exceptions.InvalidPasswordException(reason=PASSWORD_CONTAINS_EMAIL)

    @override
    async def on_after_register(self, user: AccountTable, request: Request | None = None) -> None:
        # An OAuth sign-in creates accounts that its provider has already verified
        if not user.is_verified:
            await self.request_verify(user, request)

    @override
    async def on_after_request_verify(self, user: AccountTable, token: str, request: Request | None = None) -> None:
        await self._mailer.send(AccountMail.VERIFY.to(user.email, public_url=self._public_url, token=token))

    @override
    async def on_after_forgot_password(self, user: AccountTable, token: str, request: Request | None = None) -> None:
        await self._mailer.send(AccountMail.RESET.to(user.email, public_url=self._public_url, token=token))

    @override
    async def on_after_login(
        self, user: AccountTable, request: Request | None = None, response: Response | None = None
    ) -> None:
        # Password sign-in requires a verified address, so only an OAuth sign-in that joined an unverified account
        # arrives here unverified. The provider vouches for the address now, while the password was set by whoever
        # registered it before anyone proved owning it, so that password is replaced by an unknown one.
        if user.is_verified:
            return
        unknown_password = self.password_helper.hash(self.password_helper.generate())
        await self.user_db.update(user, {'is_verified': True, 'hashed_password': unknown_password})


class XAccount(BaseModel):
    """The part of X's ``users/me`` answer that identifies the account."""

    id: str
    confirmed_email: str | None = None


class XOAuth2(BaseOAuth2[dict[str, Any]]):
    """Sign-in with X over OAuth 2.0: httpx-oauth ships no X client, so its base client is pointed at X.

    X requires PKCE, and fastapi-users' OAuth routes pass no verifier through, so the client derives one from its
    secret. The verifier never leaves the server, so an intercepted code stays useless, as PKCE intends.
    """

    display_name = 'X'

    def __init__(self, client_id: str, client_secret: str) -> None:
        super().__init__(
            client_id,
            client_secret,
            X_AUTHORIZE_ENDPOINT,
            X_TOKEN_ENDPOINT,
            X_TOKEN_ENDPOINT,
            X_REVOKE_ENDPOINT,
            name='x',
            base_scopes=X_SCOPES,
            token_endpoint_auth_method=X_CLIENT_AUTH,
            revocation_endpoint_auth_method=X_CLIENT_AUTH,
        )
        digest = hmac.new(client_secret.encode(), PKCE_CONTEXT, hashlib.sha256).digest()
        self._code_verifier = urlsafe_b64encode(digest).rstrip(b'=').decode()

    @override
    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str | None = None,
        scope: list[str] | None = None,
        code_challenge: str | None = None,
        code_challenge_method: Literal['plain', 'S256'] | None = None,
        extras_params: dict[str, Any] | None = None,
    ) -> str:
        challenge = urlsafe_b64encode(hashlib.sha256(self._code_verifier.encode()).digest()).rstrip(b'=').decode()
        return await super().get_authorization_url(redirect_uri, state, scope, challenge, PKCE_METHOD, extras_params)

    @override
    async def get_access_token(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuth2Token:
        return await super().get_access_token(code, redirect_uri, code_verifier or self._code_verifier)

    @override
    async def get_id_email(self, token: str) -> tuple[str, str | None]:
        async with self.get_httpx_client() as client:
            response = await client.get(
                X_PROFILE_ENDPOINT,
                params={'user.fields': X_EMAIL_FIELD},
                headers={**self.request_headers, 'Authorization': f'Bearer {token}'},
            )
        if response.is_error:
            raise GetIdEmailError(response=response)
        account = XAccount.model_validate(response.json()['data'])
        return account.id, account.confirmed_email


class AccountsProvider(Provider):
    """Builds the mailer, and per request the fastapi-users user manager and session strategy."""

    @provide(scope=Scope.APP)
    def mailer(self, settings: Settings) -> Mailer:
        """Send over SMTP when a host is configured, and write to the log otherwise."""
        mail = settings.mail
        if not mail.smtp_host:
            return LogMailer()
        server = SmtpServer(
            host=mail.smtp_host,
            port=mail.smtp_port,
            username=mail.smtp_username,
            password=mail.smtp_password.get_secret_value(),
            sender=mail.sender,
        )
        return SmtpMailer(server)

    @provide(scope=Scope.REQUEST)
    def user_manager(self, session: AsyncSession, mailer: Mailer, settings: Settings) -> UserManager:
        """Build the user manager over the request's session."""
        return UserManager(AccountDatabase(session), mailer=mailer, settings=settings)

    @provide(scope=Scope.REQUEST)
    def session_strategy(self, session: AsyncSession, settings: Settings) -> SessionStrategy:
        """Build the strategy that keeps session tokens in the database, so signing out revokes them."""
        return DatabaseStrategy(AccessTokenDatabase(session), lifetime_seconds=settings.auth.session_lifetime_seconds)


@inject
def get_user_manager(manager: FromDishka[UserManager]) -> Any:
    """Hand the container's user manager to fastapi-users.

    The return is typed ``Any`` because pyrefly reads fastapi-users' ``UserManagerDependency`` alias with its two
    type parameters swapped, and so rejects even the exact ``BaseUserManager[AccountTable, UUID]``.
    """
    return manager


@inject
def get_session_strategy(strategy: FromDishka[SessionStrategy]) -> SessionStrategy:
    """Hand the container's session strategy to fastapi-users."""
    return strategy


def oauth_clients(auth: AuthSettings) -> list[BaseOAuth2[Any]]:
    """Build a client for every social sign-in provider whose credentials are configured."""
    providers: list[tuple[Callable[[str, str], BaseOAuth2[Any]], OAuthClient]] = [
        (GoogleOAuth2, auth.google),
        (FacebookOAuth2, auth.facebook),
        (XOAuth2, auth.x),
    ]
    return [
        client_class(credentials.client_id, credentials.client_secret.get_secret_value())
        for client_class, credentials in providers
        if credentials.enabled
    ]


def account_routes(settings: Settings, throttle: params.Depends) -> AccountRoutes[AccountTable]:
    """Build the fastapi-users objects of one application and the routes they serve."""
    auth = settings.auth
    transport = CookieTransport(
        cookie_name=SESSION_COOKIE,
        cookie_max_age=auth.session_lifetime_seconds,
        cookie_secure=auth.cookie_secure,
        cookie_httponly=True,
        cookie_samesite='lax',
    )
    backend = AuthenticationBackend(name=AUTH_BACKEND, transport=transport, get_strategy=get_session_strategy)
    return AccountRoutes(
        users=FastAPIUsers[AccountTable, UUID](get_user_manager, [backend]),
        backend=backend,
        oauth_clients=oauth_clients(auth),
        state_secret=auth.secret,
        secure_cookies=auth.cookie_secure,
        throttle=throttle,
    )
