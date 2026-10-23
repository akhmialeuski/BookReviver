"""Provider of the accounts feature: users, sessions, OAuth clients and mail, all on fastapi-users."""

import enum
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlencode
from uuid import UUID, uuid4

import jwt
from anyio import CapacityLimiter, to_thread
from dishka import FromDishka, Provider, Scope, provide
from dishka.integrations.fastapi import inject
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, exceptions
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.password import PasswordHelper, PasswordHelperProtocol
from httpx_oauth.clients.facebook import FacebookOAuth2
from httpx_oauth.clients.google import GoogleOAuth2

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
from bookreviver.services.books import BooksService

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastapi import Request, Response, params
    from fastapi.security import OAuth2PasswordRequestForm
    from fastapi_users.schemas import BaseUserCreate
    from httpx_oauth.oauth2 import BaseOAuth2

    from bookreviver.app.settings import AuthSettings, OAuthClient

SessionStrategy = DatabaseStrategy[AccountTable, UUID, AccessTokenTable]

SESSION_COOKIE: str = 'bookreviver_session'
AUTH_BACKEND: str = 'cookie'
PASSWORD_TOO_SHORT: str = 'Use a password of at least {length} characters.'
PASSWORD_CONTAINS_EMAIL: str = 'The password must not contain the email address.'

# Names of the account fields, of the reset token claims and of the OAuth account fields that the overridden methods of
# fastapi-users read and write
EMAIL_FIELD: str = 'email'
PASSWORD_FIELD: str = 'password'
HASHED_PASSWORD_FIELD: str = 'hashed_password'
IS_VERIFIED_FIELD: str = 'is_verified'
TOKEN_SUBJECT_CLAIM: str = 'sub'
TOKEN_FINGERPRINT_CLAIM: str = 'password_fgpt'
OAUTH_NAME_FIELD: str = 'oauth_name'
ACCESS_TOKEN_FIELD: str = 'access_token'
ACCOUNT_ID_FIELD: str = 'account_id'
ACCOUNT_EMAIL_FIELD: str = 'account_email'
EXPIRES_AT_FIELD: str = 'expires_at'
REFRESH_TOKEN_FIELD: str = 'refresh_token'


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


class PasswordHasher:
    """Hashes and checks passwords with a fastapi-users helper in worker threads, a bounded number at a time.

    argon2-cffi releases the GIL, so a hash in a worker thread leaves the event loop serving. The limiter is the
    hasher's own rather than the default one of anyio, so a burst of sign-ins runs only as many argon2id hashes at once
    as the limiter allows, and never takes the threads that file and image work wait for.

    :ivar helper: The synchronous helper that does the hashing, which the user manager also generates passwords with.
    """

    def __init__(self, helper: PasswordHelperProtocol, *, limiter: CapacityLimiter) -> None:
        """Hash with ``helper``, in as many threads at once as ``limiter`` allows.

        :param helper: The synchronous password helper.
        :type helper: PasswordHelperProtocol
        :param limiter: Limiter shared by every hash and check of the application.
        :type limiter: CapacityLimiter
        """
        self.helper = helper
        self._limiter = limiter

    async def hash(self, password: str) -> str:
        """Hash ``password`` in a worker thread.

        :param password: Password to hash, as typed or an unknown one.
        :type password: str
        :returns: The hash to store.
        :rtype: str
        """
        return await to_thread.run_sync(self.helper.hash, password, limiter=self._limiter)

    async def verify(self, password: str, hashed_password: str) -> tuple[bool, str | None]:
        """Check ``password`` against ``hashed_password`` in a worker thread.

        :param password: Password to check, as typed.
        :type password: str
        :param hashed_password: Stored hash to check it against.
        :type hashed_password: str
        :returns: Whether the password matches, and a stronger hash to store instead when the stored one is outdated.
        :rtype: tuple[bool, str | None]
        """
        return await to_thread.run_sync(self.helper.verify_and_update, password, hashed_password, limiter=self._limiter)


class UserManager(UUIDIDMixin, BaseUserManager[AccountTable, UUID]):
    """fastapi-users' account rules, with mail sent through the ``Mailer`` port.

    No answer reveals whether an address is registered: registering a taken address answers like a new
    registration and mails the owner instead. An account is deleted only after its books, whose owner key refuses
    to leave them without an owner.

    Hashing a password with argon2id takes tens of milliseconds, and the ``PasswordHelper`` protocol of fastapi-users is
    synchronous, so ``BaseUserManager`` runs it inside the event loop, where it stalls every other request and event.
    The methods that hash or verify a password (``create``, ``oauth_callback``, ``forgot_password``,
    ``reset_password``, ``authenticate`` and ``_update``) are therefore overridden to mirror ``BaseUserManager`` of
    fastapi-users 15.0.5, step by step, with only the hashing moved to the worker threads of ``PasswordHasher``. Compare
    them with the upstream methods when fastapi-users is upgraded.

    :ivar password_min_length: Fewest characters a password may have.
    """

    password_min_length: int = 12

    def __init__(
        self,
        user_db: AccountDatabase,
        *,
        hasher: PasswordHasher,
        mailer: Mailer,
        books: BooksService,
        settings: Settings,
    ) -> None:
        """Build the manager over ``user_db``, signing tokens with the accounts secret.

        :param user_db: Users of the current request.
        :type user_db: AccountDatabase
        :param hasher: Hasher of the application, whose helper also generates the passwords nobody knows.
        :type hasher: PasswordHasher
        :param mailer: Port the verification, reset and already-registered messages go through.
        :type mailer: Mailer
        :param books: Books service of the request, which deletes an account's books with their files.
        :type books: BooksService
        :param settings: Application settings holding the token secret and the public URL of the web interface.
        :type settings: Settings
        """
        super().__init__(user_db, hasher.helper)
        secret = settings.auth.secret.get_secret_value()
        self.verification_token_secret = secret
        self.reset_password_token_secret = secret
        self._hasher = hasher
        self._mailer = mailer
        self._books = books
        self._public_url = settings.public_url

    @override
    async def create(
        self, user_create: BaseUserCreate, safe: bool = False, request: Request | None = None
    ) -> AccountTable:
        """Register an account, answering a taken address like a new one and mailing its owner instead.

        Mirrors ``BaseUserManager.create``, with the hash made by the hasher and the taken address handled in place of
        ``UserAlreadyExists``.

        :param user_create: Registration data, email address and password.
        :type user_create: BaseUserCreate
        :param safe: Whether to ignore the privileged fields of ``user_create``.
        :type safe: bool
        :param request: Request that triggered the registration, if any.
        :type request: Request | None
        :returns: The stored account, or for a taken address an account that is never stored.
        :rtype: AccountTable
        :raises InvalidPasswordException: If the password is too short or contains the email address.
        """
        await self.validate_password(user_create.password, user_create)

        if await self.user_db.get_by_email(user_create.email) is not None:
            # Spend the time a new account costs, and answer with an account that is never stored
            await self._hasher.hash(user_create.password)
            await self._mailer.send(AccountMail.ALREADY_REGISTERED.to(user_create.email, public_url=self._public_url))
            return AccountTable(
                id=uuid4(), email=user_create.email, is_active=True, is_superuser=False, is_verified=False
            )

        user_dict = user_create.create_update_dict() if safe else user_create.create_update_dict_superuser()
        password = user_dict.pop(PASSWORD_FIELD)
        user_dict[HASHED_PASSWORD_FIELD] = await self._hasher.hash(password)
        created_user = await self.user_db.create(user_dict)
        await self.on_after_register(created_user, request)
        return created_user

    # fastapi-users 15.0.5 declares ``UserOAuthProtocol`` with ``Generic`` instead of ``Protocol`` in its bases
    # (fastapi_users/models.py:33), so by PEP 544 it is a plain class, and ``UOAP``, which it bounds, accepts only its
    # subclasses. ``AccountTable`` has every member it asks for but does not inherit it, so ty and pyrefly refuse this
    # override of a method bound to ``UOAP``, the calls of the OAuth methods of ``user_db`` and the user they return.
    # Once fastapi-users declares it a protocol, ty reports these ignores as unused: remove them then, together with the
    # pyrefly ones on the same calls.
    @override
    async def oauth_callback(  # ty: ignore[invalid-method-override]
        self,
        oauth_name: str,
        access_token: str,
        account_id: str,
        account_email: str,
        expires_at: int | None = None,
        refresh_token: str | None = None,
        request: Request | None = None,
        *,
        associate_by_email: bool = False,
        is_verified_by_default: bool = False,
    ) -> AccountTable:
        """Sign in the account a provider vouches for, creating it with a password nobody knows when there is none.

        Mirrors ``BaseUserManager.oauth_callback``, with the hash of the unknown password made by the hasher.

        :param oauth_name: Name of the OAuth client.
        :type oauth_name: str
        :param access_token: Access token the provider issued.
        :type access_token: str
        :param account_id: Identifier of the account at the provider.
        :type account_id: str
        :param account_email: Email address of the account at the provider.
        :type account_email: str
        :param expires_at: When the access token expires, as a Unix timestamp, if the provider says.
        :type expires_at: int | None
        :param refresh_token: Token that renews the access token, if the provider issued one.
        :type refresh_token: str | None
        :param request: Request that triggered the sign-in, if any.
        :type request: Request | None
        :param associate_by_email: Whether to join an existing account of the same address.
        :type associate_by_email: bool
        :param is_verified_by_default: Whether a new account starts with its address verified.
        :type is_verified_by_default: bool
        :returns: The signed-in account.
        :rtype: AccountTable
        :raises UserAlreadyExists: If an account of the address exists and ``associate_by_email`` is off.
        """
        oauth_account_dict: dict[str, Any] = {
            OAUTH_NAME_FIELD: oauth_name,
            ACCESS_TOKEN_FIELD: access_token,
            ACCOUNT_ID_FIELD: account_id,
            ACCOUNT_EMAIL_FIELD: account_email,
            EXPIRES_AT_FIELD: expires_at,
            REFRESH_TOKEN_FIELD: refresh_token,
        }

        try:
            user = await self.get_by_oauth_account(oauth_name, account_id)
        except exceptions.UserNotExists:
            try:
                user = await self.get_by_email(account_email)
                if not associate_by_email:
                    raise exceptions.UserAlreadyExists from None
                user = await self.user_db.add_oauth_account(user, oauth_account_dict)  # ty: ignore[invalid-argument-type]  # pyrefly: ignore[bad-specialization]
            except exceptions.UserNotExists:
                user_dict = {
                    EMAIL_FIELD: account_email,
                    HASHED_PASSWORD_FIELD: await self._hasher.hash(self.password_helper.generate()),
                    IS_VERIFIED_FIELD: is_verified_by_default,
                }
                user = await self.user_db.create(user_dict)
                user = await self.user_db.add_oauth_account(user, oauth_account_dict)  # ty: ignore[invalid-argument-type]  # pyrefly: ignore[bad-specialization]
                await self.on_after_register(user, request)
        else:
            for existing_oauth_account in user.oauth_accounts:
                if existing_oauth_account.account_id == account_id and existing_oauth_account.oauth_name == oauth_name:
                    user = await self.user_db.update_oauth_account(user, existing_oauth_account, oauth_account_dict)  # ty: ignore[invalid-argument-type]  # pyrefly: ignore[bad-specialization]

        return user  # ty: ignore[unsound-return-statement]

    @override
    async def forgot_password(self, user: AccountTable, request: Request | None = None) -> None:
        """Start a password reset, fingerprinting the current hash with a second one made off the event loop.

        Mirrors ``BaseUserManager.forgot_password``.

        :param user: The account that forgot its password.
        :type user: AccountTable
        :param request: Request that triggered the reset, if any.
        :type request: Request | None
        :raises UserInactive: If the account is inactive.
        """
        if not user.is_active:
            raise exceptions.UserInactive
        token_data = {
            TOKEN_SUBJECT_CLAIM: str(user.id),
            TOKEN_FINGERPRINT_CLAIM: await self._hasher.hash(user.hashed_password),
            'aud': self.reset_password_token_audience,
        }
        token = generate_jwt(token_data, self.reset_password_token_secret, self.reset_password_token_lifetime_seconds)
        await self.on_after_forgot_password(user, token, request)

    @override
    async def reset_password(self, token: str, password: str, request: Request | None = None) -> AccountTable:
        """Set a new password for the account the reset token was issued to, once the fingerprint in it matches.

        Mirrors ``BaseUserManager.reset_password``.

        :param token: Token mailed by ``forgot_password``.
        :type token: str
        :param password: The new password.
        :type password: str
        :param request: Request that triggered the reset, if any.
        :type request: Request | None
        :returns: The account with the new password.
        :rtype: AccountTable
        :raises InvalidResetPasswordToken: If the token is invalid, expired or issued for an earlier password.
        :raises UserInactive: If the account is inactive.
        :raises InvalidPasswordException: If the new password is invalid.
        """
        try:
            data = decode_jwt(token, self.reset_password_token_secret, [self.reset_password_token_audience])
        except jwt.PyJWTError:
            raise exceptions.InvalidResetPasswordToken from None
        try:
            user_id = data[TOKEN_SUBJECT_CLAIM]
            password_fingerprint = data[TOKEN_FINGERPRINT_CLAIM]
        except KeyError:
            raise exceptions.InvalidResetPasswordToken from None
        try:
            parsed_id = self.parse_id(user_id)
        except exceptions.InvalidID:
            raise exceptions.InvalidResetPasswordToken from None

        user = await self.get(parsed_id)
        valid_password_fingerprint, _ = await self._hasher.verify(user.hashed_password, password_fingerprint)
        if not valid_password_fingerprint:
            raise exceptions.InvalidResetPasswordToken
        if not user.is_active:
            raise exceptions.UserInactive

        updated_user = await self._update(user, {PASSWORD_FIELD: password})
        await self.on_after_reset_password(user, request)
        return updated_user

    @override
    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> AccountTable | None:
        """Return the account the credentials belong to, upgrading its stored hash when it is outdated.

        Mirrors ``BaseUserManager.authenticate``: an unknown address still costs one hash, so the answer takes about
        as long as for a known one.

        :param credentials: The login form, whose user name is the email address.
        :type credentials: OAuth2PasswordRequestForm
        :returns: The account when the password matches, otherwise ``None``.
        :rtype: AccountTable | None
        """
        try:
            user = await self.get_by_email(credentials.username)
        except exceptions.UserNotExists:
            # Run the hasher to mitigate a timing attack, as Django does: https://code.djangoproject.com/ticket/20760
            await self._hasher.hash(credentials.password)
            return None

        verified, updated_password_hash = await self._hasher.verify(credentials.password, user.hashed_password)
        if not verified:
            return None
        # Replace the stored hash with a more robust one if needed
        if updated_password_hash is not None:
            await self.user_db.update(user, {HASHED_PASSWORD_FIELD: updated_password_hash})
        return user

    @override
    async def _update(self, user: AccountTable, update_dict: dict[str, Any]) -> AccountTable:
        """Apply validated changes to the account, hashing a new password off the event loop.

        Mirrors ``BaseUserManager._update``.

        :param user: The account to change.
        :type user: AccountTable
        :param update_dict: Fields to change, a new password under ``password``.
        :type update_dict: dict[str, Any]
        :returns: The changed account.
        :rtype: AccountTable
        :raises UserAlreadyExists: If a new email address is taken.
        :raises InvalidPasswordException: If a new password is invalid.
        """
        validated_update_dict: dict[str, Any] = {}
        for field, value in update_dict.items():
            if field == EMAIL_FIELD and value != user.email:
                try:
                    await self.get_by_email(value)
                except exceptions.UserNotExists:
                    validated_update_dict[EMAIL_FIELD] = value
                    validated_update_dict[IS_VERIFIED_FIELD] = False
                else:
                    raise exceptions.UserAlreadyExists
            elif field == PASSWORD_FIELD and value is not None:
                await self.validate_password(value, user)
                validated_update_dict[HASHED_PASSWORD_FIELD] = await self._hasher.hash(value)
            else:
                validated_update_dict[field] = value
        return await self.user_db.update(user, validated_update_dict)

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
        unknown_password = await self._hasher.hash(self.password_helper.generate())
        await self.user_db.update(user, {IS_VERIFIED_FIELD: True, HASHED_PASSWORD_FIELD: unknown_password})

    @override
    async def on_before_delete(self, user: AccountTable, request: Request | None = None) -> None:
        """Delete every book of the account with its files, so the account can be deleted after them.

        A database cascade from the account would remove the rows of its books and leave their files behind, so
        the owner key restricts, and the books are deleted here through ``BooksService``.

        :param user: Account about to be deleted.
        :type user: AccountTable
        :param request: Request that triggered the deletion, if any.
        :type request: Request | None
        """
        await self._books.delete_all(Actor(account_id=AccountId(user.id)))


class AccountsProvider(Provider):
    """Builds the mailer and the password hasher, and per request the user manager and the session strategy."""

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

    @provide(scope=Scope.APP)
    def password_helper(self) -> PasswordHelperProtocol:
        """Build the helper of fastapi-users, which hashes with argon2id and still checks the bcrypt hashes of before.

        :returns: The default password helper.
        :rtype: PasswordHelperProtocol
        """
        return PasswordHelper()

    @provide(scope=Scope.APP)
    def password_hasher(self, helper: PasswordHelperProtocol, settings: Settings) -> PasswordHasher:
        """Build the one hasher of the application, so its limiter bounds every hash of every request together.

        :param helper: Helper that does the hashing.
        :type helper: PasswordHelperProtocol
        :param settings: Application settings holding how many hashes may run at once.
        :type settings: Settings
        :returns: The hasher of the application.
        :rtype: PasswordHasher
        """
        return PasswordHasher(helper, limiter=CapacityLimiter(settings.auth.password_hash_concurrency))

    @provide(scope=Scope.REQUEST)
    def account_database(self, uow: SqlAlchemyUnitOfWork) -> AccountDatabase:
        """Build the users of the request, read and written through its unit of work.

        :param uow: Unit of work of the request, whose transactions the account reads and writes run in.
        :type uow: SqlAlchemyUnitOfWork
        :returns: The users of the request.
        :rtype: AccountDatabase
        """
        return AccountDatabase(uow)

    @provide(scope=Scope.REQUEST)
    def user_manager(
        self,
        user_db: AccountDatabase,
        hasher: PasswordHasher,
        mailer: Mailer,
        books: BooksService,
        settings: Settings,
    ) -> UserManager:
        """Build the user manager over the users of the request.

        :param user_db: Users of the request.
        :type user_db: AccountDatabase
        :param hasher: Hasher of the application, shared by every request.
        :type hasher: PasswordHasher
        :param mailer: Port the account messages go through.
        :type mailer: Mailer
        :param books: Books service of the request, which deletes the books of a deleted account.
        :type books: BooksService
        :param settings: Application settings holding the secret and the public URL.
        :type settings: Settings
        :returns: The user manager of the request.
        :rtype: UserManager
        """
        return UserManager(user_db, hasher=hasher, mailer=mailer, books=books, settings=settings)

    @provide(scope=Scope.REQUEST)
    def session_strategy(self, uow: SqlAlchemyUnitOfWork, settings: Settings) -> SessionStrategy:
        """Build the strategy that keeps session tokens in the database, so signing out revokes them.

        :param uow: Unit of work of the request, whose transactions the token reads and writes run in.
        :type uow: SqlAlchemyUnitOfWork
        :param settings: Application settings holding the session lifetime.
        :type settings: Settings
        :returns: The session strategy of the request.
        :rtype: SessionStrategy
        """
        return DatabaseStrategy(AccessTokenDatabase(uow), lifetime_seconds=settings.auth.session_lifetime_seconds)


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
