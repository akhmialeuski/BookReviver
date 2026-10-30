"""Fakes and steps of the accounts feature: a recording mailer and password helper with their providers, and a visitor."""

import enum
import threading
from typing import TYPE_CHECKING, override
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from attrs import frozen
from dishka import Provider, Scope, provide
from fastapi_users.password import PasswordHelper, PasswordHelperProtocol

from bookreviver.app.providers.accounts import AccountMail
from bookreviver.ports.runtime import Mailer

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.accounts import MailMessage

TOKEN_PARAMETER: str = 'token'
EMAIL_FIELD: str = 'email'
PASSWORD_FIELD: str = 'password'
DETAIL_FIELD: str = 'detail'
PASSWORD: str = 'correct horse battery staple'
AUTH_PATH: str = '/api/v1/auth'
REGISTER_PATH: str = f'{AUTH_PATH}/register'
VERIFY_PATH: str = f'{AUTH_PATH}/verify'
LOGIN_PATH: str = f'{AUTH_PATH}/login'
LOGOUT_PATH: str = f'{AUTH_PATH}/logout'
FORGOT_PATH: str = f'{AUTH_PATH}/forgot-password'
RESET_PATH: str = f'{AUTH_PATH}/reset-password'
USERS_PATH: str = '/api/v1/users'
ME_PATH: str = f'{USERS_PATH}/me'
CSRF_COOKIE: str = 'csrftoken'
CSRF_HEADER: str = 'x-csrftoken'


@frozen
class SignedInUser:
    """The user a request of a test is signed in as, of which the API reads only the identifier.

    :ivar id: Identifier of the account.
    """

    id: UUID


class RecordingMailer(Mailer):
    """Keeps every message instead of sending it.

    :ivar sent: Messages in the order they were sent.
    """

    def __init__(self) -> None:
        """Start with no messages sent."""
        self.sent: list[MailMessage] = []

    @override
    async def send(self, message: MailMessage) -> None:
        """Record the message.

        :param message: Message that would have been sent.
        :type message: MailMessage
        """
        self.sent.append(message)

    def token(self, subject: str) -> str:
        """Return the token in the link of the last message with this subject.

        :param subject: Subject line of the message to read.
        :type subject: str
        :returns: The ``token`` query parameter of the first link in the message.
        :rtype: str
        """
        message = next(message for message in reversed(self.sent) if message.subject == subject)
        link = next(word for word in message.body.split() if word.startswith('http'))
        return parse_qs(urlsplit(link).query)[TOKEN_PARAMETER][0]


class RecordingMailerProvider(Provider):
    """Replaces the application's mailer with a given recording one."""

    def __init__(self, mailer: RecordingMailer) -> None:
        """Provide ``mailer`` in place of the application's own.

        :param mailer: Recording mailer of the test.
        :type mailer: RecordingMailer
        """
        super().__init__()
        self._mailer = mailer

    @provide(scope=Scope.APP, override=True)
    def mailer(self) -> Mailer:
        """Return the recording mailer.

        :returns: The mailer given to the constructor.
        :rtype: Mailer
        """
        return self._mailer


class HashStep(enum.StrEnum):
    """The calls of the password helper that take long enough to stall an event loop."""

    HASH = 'hash'
    VERIFY = 'verify'


class HashCalls:
    """Keeps which password helper call ran on which thread, in call order."""

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self._calls: list[tuple[HashStep, int]] = []

    def record(self, step: HashStep) -> None:
        """Record a call made now, on the calling thread.

        :param step: The helper call being made.
        :type step: HashStep
        """
        self._calls.append((step, threading.get_ident()))

    def clear(self) -> None:
        """Forget the calls so far, so a test records only the request it is about."""
        self._calls.clear()

    @property
    def steps(self) -> list[HashStep]:
        """The recorded calls in the order they were made, one step per call."""
        return [step for step, _ in self._calls]

    @property
    def threads(self) -> set[int]:
        """The identifiers of the threads the recorded calls ran on."""
        return {thread for _, thread in self._calls}


class RecordingPasswordHelper(PasswordHelper):
    """The real password helper, noting the thread of every hash and every check.

    :ivar calls: Record the calls are noted in.
    """

    def __init__(self, calls: HashCalls) -> None:
        """Note the calls of this helper in ``calls``.

        :param calls: Record shared by the helpers of one test.
        :type calls: HashCalls
        """
        super().__init__()
        self.calls = calls

    @override
    def hash(self, password: str) -> str:
        """Note the call, then hash ``password``.

        :param password: Password to hash.
        :type password: str
        :returns: The hash.
        :rtype: str
        """
        self.calls.record(HashStep.HASH)
        return super().hash(password)

    @override
    def verify_and_update(self, plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
        """Note the call, then check ``plain_password`` against ``hashed_password``.

        :param plain_password: Password to check.
        :type plain_password: str
        :param hashed_password: Stored hash to check it against.
        :type hashed_password: str
        :returns: Whether it matches, and a stronger hash when the stored one is outdated.
        :rtype: tuple[bool, str | None]
        """
        self.calls.record(HashStep.VERIFY)
        return super().verify_and_update(plain_password, hashed_password)


class RecordingPasswordHelperProvider(Provider):
    """Replaces the application's password helper with one that records into a given record."""

    def __init__(self, calls: HashCalls) -> None:
        """Provide a recording helper writing into ``calls`` in place of the application's own.

        :param calls: Record of the test.
        :type calls: HashCalls
        """
        super().__init__()
        self._calls = calls

    @provide(scope=Scope.APP, override=True)
    def password_helper(self) -> PasswordHelperProtocol:
        """Return a recording password helper.

        :returns: A helper noting its calls in the record given to the constructor.
        :rtype: PasswordHelperProtocol
        """
        return RecordingPasswordHelper(self._calls)


class Visitor:
    """Drives the account routes over HTTP the way the web interface does, reading tokens from the mail.

    :ivar client: Client that sends the requests and keeps the session cookie.
    :ivar mailer: Recording mailer the confirmation tokens are read from.
    """

    def __init__(self, client: httpx.AsyncClient, mailer: RecordingMailer) -> None:
        """Drive the routes through ``client``, reading mail from ``mailer``.

        :param client: Client talking to the application, signed out and sending the CSRF header.
        :type client: httpx.AsyncClient
        :param mailer: Recording mailer of the application under test.
        :type mailer: RecordingMailer
        """
        self.client = client
        self.mailer = mailer

    async def register(self, email: str, password: str = PASSWORD) -> httpx.Response:
        """Register an account.

        :param email: Address to register.
        :type email: str
        :param password: Password of the account.
        :type password: str
        :returns: Response of the registration route.
        :rtype: httpx.Response
        """
        return await self.client.post(REGISTER_PATH, json={EMAIL_FIELD: email, PASSWORD_FIELD: password})

    async def verify(self) -> httpx.Response:
        """Confirm the address with the token of the last verification mail.

        :returns: Response of the verification route.
        :rtype: httpx.Response
        """
        return await self.client.post(
            VERIFY_PATH, json={TOKEN_PARAMETER: self.mailer.token(AccountMail.VERIFY.subject)}
        )

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        """Sign in with the login form.

        :param email: Address of the account, sent as the form's user name.
        :type email: str
        :param password: Password of the account.
        :type password: str
        :returns: Response of the login route, carrying the session cookie on success.
        :rtype: httpx.Response
        """
        return await self.client.post(LOGIN_PATH, data={'username': email, PASSWORD_FIELD: password})

    async def sign_up(self, email: str, password: str = PASSWORD) -> httpx.Response:
        """Register, confirm the address and sign in, returning the login response.

        :param email: Address to register.
        :type email: str
        :param password: Password of the account.
        :type password: str
        :returns: Response of the login route.
        :rtype: httpx.Response
        """
        (await self.register(email, password)).raise_for_status()
        (await self.verify()).raise_for_status()
        return await self.login(email, password)
