"""Provider of the accounts feature: users, sessions, OAuth clients and mail, all on fastapi-users."""

import enum
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlencode
from uuid import UUID, uuid4

from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import inject
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from httpx_oauth.clients.facebook import FacebookOAuth2
from httpx_oauth.clients.google import GoogleOAuth2
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.adapters.mail.log import LogMailer
from bookreviver.adapters.mail.smtp import SmtpMailer, SmtpServer
from bookreviver.adapters.persistence.sqlalchemy.accounts import (
    AccessTokenDatabase,
    AccessTokenTable,
    AccountDatabase,
    AccountTable,
)
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.api.routers.accounts import AccountRoutes
from bookreviver.app.settings import Settings
from bookreviver.domain.entities import Actor
from bookreviver.domain.ids import AccountId
from bookreviver.domain.values import MailMessage
from bookreviver.ports.runtime import Mailer
from bookreviver.services.projects import ProjectService

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastapi import Request, Response, params
    from fastapi_users.schemas import BaseUserCreate
    from httpx_oauth.oauth2 import BaseOAuth2

    from bookreviver.app.settings import AuthSettings, OAuthClient

SessionStrategy = DatabaseStrategy[AccountTable, UUID, AccessTokenTable]

SESSION_COOKIE: str = 'bookreviver_session'
AUTH_BACKEND: str = 'cookie'
PASSWORD_TOO_SHORT: str = 'Use a password of at least {length} characters.'
PASSWORD_CONTAINS_EMAIL: str = 'The password must not contain the email address.'


class AccountMail(enum.Enum):
    """Messages about an account, each linking to a page of the web interface.

    :ivar page: Path of the page the message links to, under the public URL.
    :ivar subject: Subject line.
    :ivar body: Body text with a ``{link}`` placeholder for the link.
    """

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
        """Keep the three parts of the message that each member fixes.

        :param page: Path of the page the message links to.
        :type page: str
        :param subject: Subject line.
        :type subject: str
        :param body: Body text with a ``{link}`` placeholder.
        :type body: str
        """
        self.page = page
        self.subject = subject
        self.body = body

    def to(self, address: str, *, public_url: str, token: str = '') -> MailMessage:
        """Build the message for ``address``, its link carrying ``token`` when one is given.

        :param address: Email address of the recipient.
        :type address: str
        :param public_url: Address of the web interface the link points into.
        :type public_url: str
        :param token: Token to put in the link's query, or empty for a link without one.
        :type token: str
        :returns: The message ready to send.
        :rtype: MailMessage
        """
        query = f'?{urlencode({"token": token})}' if token else ''
        link = f'{public_url.rstrip("/")}/{self.page}{query}'
        return MailMessage(to=address, subject=self.subject, body=self.body.format(link=link))


class UserManager(UUIDIDMixin, BaseUserManager[AccountTable, UUID]):
    """fastapi-users' account rules, with mail sent through the ``Mailer`` port.

    No answer reveals whether an address is registered: registering a taken address answers like a new
    registration and mails the owner instead. An account is deleted only after its projects, whose owner key refuses
    to leave them without an owner.

    :ivar password_min_length: Fewest characters a password may have.
    """

    password_min_length: int = 12

    def __init__(
        self, user_db: AccountDatabase, *, mailer: Mailer, projects: ProjectService, settings: Settings
    ) -> None:
        """Build the manager over ``user_db``, signing tokens with the accounts secret.

        :param user_db: Users of the current request's session.
        :type user_db: AccountDatabase
        :param mailer: Port the verification, reset and already-registered messages go through.
        :type mailer: Mailer
        :param projects: Project service of the request, which deletes an account's projects with their files.
        :type projects: ProjectService
        :param settings: Application settings holding the token secret and the public URL of the web interface.
        :type settings: Settings
        """
        super().__init__(user_db)
        secret = settings.auth.secret.get_secret_value()
        self.verification_token_secret = secret
        self.reset_password_token_secret = secret
        self._mailer = mailer
        self._projects = projects
        self._public_url = settings.public_url

    @override
    async def create(
        self, user_create: BaseUserCreate, safe: bool = False, request: Request | None = None
    ) -> AccountTable:
        """Register an account, answering a taken address like a new one and mailing its owner instead.

        :param user_create: Registration data, email address and password.
        :type user_create: BaseUserCreate
        :param safe: Whether to ignore the privileged fields of ``user_create``.
        :type safe: bool
        :param request: Request that triggered the registration, if any.
        :type request: Request | None
        :returns: The stored account, or for a taken address an account that is never stored.
        :rtype: AccountTable
        """
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
        """Require a password of at least ``password_min_length`` characters that does not contain the address.

        :param password: Password to check, as typed.
        :type password: str
        :param user: Registration data or account the password is for.
        :type user: BaseUserCreate | AccountTable
        :raises InvalidPasswordException: If the password is too short or contains the email address.
        """
        if len(password) < self.password_min_length:
            raise exceptions.InvalidPasswordException(reason=PASSWORD_TOO_SHORT.format(length=self.password_min_length))
        if user.email.lower() in password.lower():
            raise exceptions.InvalidPasswordException(reason=PASSWORD_CONTAINS_EMAIL)

    @override
    async def on_after_register(self, user: AccountTable, request: Request | None = None) -> None:
        """Mail a verification link to an account whose address nobody has verified yet.

        :param user: The account just registered.
        :type user: AccountTable
        :param request: Request that triggered the registration, if any.
        :type request: Request | None
        """
        # An OAuth sign-in creates accounts that its provider has already verified
        if not user.is_verified:
            await self.request_verify(user, request)

    @override
    async def on_after_request_verify(self, user: AccountTable, token: str, request: Request | None = None) -> None:
        """Mail the account the link that confirms its address.

        :param user: Account asking to verify its address.
        :type user: AccountTable
        :param token: Verification token to put in the link.
        :type token: str
        :param request: Request that triggered the verification request, if any.
        :type request: Request | None
        """
        await self._mailer.send(AccountMail.VERIFY.to(user.email, public_url=self._public_url, token=token))

    @override
    async def on_after_forgot_password(self, user: AccountTable, token: str, request: Request | None = None) -> None:
        """Mail the account the link that lets it choose a new password.

        :param user: Account that forgot its password.
        :type user: AccountTable
        :param token: Reset token to put in the link.
        :type token: str
        :param request: Request that triggered the reset, if any.
        :type request: Request | None
        """
        await self._mailer.send(AccountMail.RESET.to(user.email, public_url=self._public_url, token=token))

    @override
    async def on_after_login(
        self, user: AccountTable, request: Request | None = None, response: Response | None = None
    ) -> None:
        """Verify an account a provider signed in, and replace the password of whoever registered it.

        :param user: Account that just signed in.
        :type user: AccountTable
        :param request: Request that triggered the sign-in, if any.
        :type request: Request | None
        :param response: Response carrying the session cookie, if any.
        :type response: Response | None
        """
        # Password sign-in requires a verified address, so only an OAuth sign-in that joined an unverified account
        # arrives here unverified. The provider vouches for the address now, while the password was set by whoever
        # registered it before anyone proved owning it, so that password is replaced by an unknown one.
        if user.is_verified:
            return
        unknown_password = self.password_helper.hash(self.password_helper.generate())
        await self.user_db.update(user, {'is_verified': True, 'hashed_password': unknown_password})

    @override
    async def on_before_delete(self, user: AccountTable, request: Request | None = None) -> None:
        """Delete every project of the account with its files, so the account can be deleted after them.

        A database cascade from the account would remove the rows of its projects and leave their files behind, so
        the owner key restricts, and the projects are deleted here through ``ProjectService``.

        :param user: Account about to be deleted.
        :type user: AccountTable
        :param request: Request that triggered the deletion, if any.
        :type request: Request | None
        """
        await self._projects.delete_all(Actor(account_id=AccountId(user.id)))


class AccountsProvider(Provider):
    """Builds the mailer, and per request the fastapi-users user manager and session strategy."""

    @provide(scope=Scope.APP)
    def mailer(self, settings: Settings) -> Mailer:
        """Send over SMTP when a host is configured, and write to the log otherwise.

        :param settings: Application settings holding the mail section.
        :type settings: Settings
        :returns: The SMTP mailer, or the log mailer when no host is set.
        :rtype: Mailer
        """
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
    def user_manager(
        self,
        session: AsyncSession,
        uow: SqlAlchemyUnitOfWork,
        mailer: Mailer,
        projects: ProjectService,
        settings: Settings,
    ) -> UserManager:
        """Build the user manager over the request's session and the unit of work that guards it.

        :param session: Database session of the request.
        :type session: AsyncSession
        :param uow: Unit of work of that session, whose blocks the account writes run in.
        :type uow: SqlAlchemyUnitOfWork
        :param mailer: Port the account messages go through.
        :type mailer: Mailer
        :param projects: Project service of the request, which deletes the projects of a deleted account.
        :type projects: ProjectService
        :param settings: Application settings holding the secret and the public URL.
        :type settings: Settings
        :returns: The user manager of the request.
        :rtype: UserManager
        """
        return UserManager(AccountDatabase(session, uow), mailer=mailer, projects=projects, settings=settings)

    @provide(scope=Scope.REQUEST)
    def session_strategy(self, session: AsyncSession, uow: SqlAlchemyUnitOfWork, settings: Settings) -> SessionStrategy:
        """Build the strategy that keeps session tokens in the database, so signing out revokes them.

        :param session: Database session of the request.
        :type session: AsyncSession
        :param uow: Unit of work of that session, whose blocks the token writes run in.
        :type uow: SqlAlchemyUnitOfWork
        :param settings: Application settings holding the session lifetime.
        :type settings: Settings
        :returns: The session strategy of the request.
        :rtype: SessionStrategy
        """
        return DatabaseStrategy(
            AccessTokenDatabase(session, uow), lifetime_seconds=settings.auth.session_lifetime_seconds
        )


@inject
def get_user_manager(manager: FromDishka[UserManager]) -> Any:
    """Hand the container's user manager to fastapi-users.

    The return is typed ``Any`` because pyrefly reads fastapi-users' ``UserManagerDependency`` alias with its two
    type parameters swapped, and so rejects even the exact ``BaseUserManager[AccountTable, UUID]``.

    :param manager: User manager the container builds for the request.
    :type manager: UserManager
    :returns: The same manager.
    :rtype: Any
    """
    return manager


@inject
def get_session_strategy(strategy: FromDishka[SessionStrategy]) -> SessionStrategy:
    """Hand the container's session strategy to fastapi-users.

    :param strategy: Session strategy the container builds for the request.
    :type strategy: SessionStrategy
    :returns: The same strategy.
    :rtype: SessionStrategy
    """
    return strategy


def oauth_clients(auth: AuthSettings) -> list[BaseOAuth2[Any]]:
    """Build a client for every social sign-in provider whose credentials are configured.

    :param auth: Accounts settings holding the credentials of each provider.
    :type auth: AuthSettings
    :returns: Clients of the enabled providers, Google and Facebook.
    :rtype: list[BaseOAuth2[Any]]
    """
    providers: list[tuple[Callable[[str, str], BaseOAuth2[Any]], OAuthClient]] = [
        (GoogleOAuth2, auth.google),
        (FacebookOAuth2, auth.facebook),
    ]
    return [
        client_class(credentials.client_id, credentials.client_secret.get_secret_value())
        for client_class, credentials in providers
        if credentials.enabled
    ]


def account_routes(
    settings: Settings, throttle: params.Depends, social_clients: Sequence[BaseOAuth2[Any]] | None = None
) -> AccountRoutes[AccountTable]:
    """Build the fastapi-users objects of one application and the routes they serve.

    :param settings: Application settings holding the accounts section.
    :type settings: Settings
    :param throttle: Dependency counting attempts at the sign-in routes.
    :type throttle: params.Depends
    :param social_clients: Clients to offer instead of the ones the credentials in the settings enable, or ``None``.
    :type social_clients: Sequence[BaseOAuth2[Any]] | None
    :returns: The routes with the objects they were built from.
    :rtype: AccountRoutes[AccountTable]
    """
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
        oauth_clients=oauth_clients(auth) if social_clients is None else social_clients,
        state_secret=auth.secret,
        public_url=settings.public_url,
        secure_cookies=auth.cookie_secure,
        throttle=throttle,
    )
