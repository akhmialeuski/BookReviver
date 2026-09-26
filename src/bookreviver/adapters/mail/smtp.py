"""A mailer that sends plain-text messages through an SMTP server with aiosmtplib."""

from email.message import EmailMessage
from typing import TYPE_CHECKING, override

import aiosmtplib
from attrs import field, frozen

from bookreviver.ports.runtime import Mailer

if TYPE_CHECKING:
    from bookreviver.domain.values import MailMessage


@frozen(kw_only=True)
class SmtpServer:
    """Where and as whom to send; STARTTLS is used whenever the server offers it."""

    host: str
    port: int
    username: str = ''
    password: str = field(default='', repr=False)
    sender: str


class SmtpMailer(Mailer):
    """Opens one SMTP connection per message, which suits the few account messages sent."""

    def __init__(self, server: SmtpServer) -> None:
        self._server = server

    @override
    async def send(self, message: MailMessage) -> None:
        email = EmailMessage()
        email['From'] = self._server.sender
        email['To'] = message.to
        email['Subject'] = message.subject
        email.set_content(message.body)
        await aiosmtplib.send(
            email,
            hostname=self._server.host,
            port=self._server.port,
            username=self._server.username or None,
            password=self._server.password or None,
        )
