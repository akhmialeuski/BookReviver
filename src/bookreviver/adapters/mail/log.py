"""A mailer that writes each message to the log, for development without an SMTP server."""

import logging
from typing import TYPE_CHECKING, override

from bookreviver.ports.runtime import Mailer

if TYPE_CHECKING:
    from bookreviver.domain.values import MailMessage

logger = logging.getLogger(__name__)


class LogMailer(Mailer):
    """Logs every message in full, links included, so a developer can follow them."""

    @override
    async def send(self, message: MailMessage) -> None:
        logger.info('Mail to %s: %s\n%s', message.to, message.subject, message.body)
