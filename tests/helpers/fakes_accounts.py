"""Fakes and steps of the accounts feature: a recording mailer, its provider, and a visitor using the routes."""

from typing import TYPE_CHECKING, override
from urllib.parse import parse_qs, urlsplit

from dishka import Provider, Scope, provide

from bookreviver.app.providers.accounts import AccountMail
from bookreviver.ports.runtime import Mailer

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.values import MailMessage

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
ME_PATH: str = '/api/v1/users/me'
CSRF_COOKIE: str = 'csrftoken'
CSRF_HEADER: str = 'x-csrftoken'


class RecordingMailer(Mailer):
    """Keeps every message instead of sending it."""

    def __init__(self) -> None:
        self.sent: list[MailMessage] = []

    @override
    async def send(self, message: MailMessage) -> None:
        self.sent.append(message)

    def token(self, subject: str) -> str:
        """Return the token in the link of the last message with this subject."""
        message = next(message for message in reversed(self.sent) if message.subject == subject)
        link = next(word for word in message.body.split() if word.startswith('http'))
        return parse_qs(urlsplit(link).query)[TOKEN_PARAMETER][0]


class RecordingMailerProvider(Provider):
    """Replaces the application's mailer with a given recording one."""

    def __init__(self, mailer: RecordingMailer) -> None:
        super().__init__()
        self._mailer = mailer

    @provide(scope=Scope.APP, override=True)
    def mailer(self) -> Mailer:
        """Return the recording mailer."""
        return self._mailer


class Visitor:
    """Drives the account routes over HTTP the way the web interface does, reading tokens from the mail."""

    def __init__(self, client: httpx.AsyncClient, mailer: RecordingMailer) -> None:
        self.client = client
        self.mailer = mailer

    async def register(self, email: str, password: str = PASSWORD) -> httpx.Response:
        """Register an account."""
        return await self.client.post(REGISTER_PATH, json={EMAIL_FIELD: email, PASSWORD_FIELD: password})

    async def verify(self) -> httpx.Response:
        """Confirm the address with the token of the last verification mail."""
        return await self.client.post(
            VERIFY_PATH, json={TOKEN_PARAMETER: self.mailer.token(AccountMail.VERIFY.subject)}
        )

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        """Sign in with the login form."""
        return await self.client.post(LOGIN_PATH, data={'username': email, PASSWORD_FIELD: password})

    async def sign_up(self, email: str, password: str = PASSWORD) -> httpx.Response:
        """Register, confirm the address and sign in, returning the login response."""
        (await self.register(email, password)).raise_for_status()
        (await self.verify()).raise_for_status()
        return await self.login(email, password)
