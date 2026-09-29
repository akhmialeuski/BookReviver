"""Tests for the mail adapters and the choice between them."""

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.mail.log import LogMailer
from bookreviver.adapters.mail.smtp import SmtpMailer, SmtpServer
from bookreviver.app.container import build_container
from bookreviver.domain.values import MailMessage
from bookreviver.ports.runtime import Mailer

if TYPE_CHECKING:
    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

SMTP_SEND: str = 'bookreviver.adapters.mail.smtp.aiosmtplib.send'
SMTP_HOST: str = 'smtp.example.org'
SMTP_PORT: int = 587
SENDER: str = 'BookReviver <no-reply@example.org>'
SMTP_USER: str = 'mailer'
MESSAGE: MailMessage = MailMessage(
    to='reader@example.org', subject='Confirm your email address', body='https://bookreviver.example/verify-email'
)


class TestLogMailer:
    """Tests for LogMailer.send()."""

    async def test_message_is_logged_in_full(self, caplog: pytest.LogCaptureFixture) -> None:
        """Verify the recipient, subject and body, link included, reach the log.

        :param caplog: Pytest's log capture.
        :type caplog: pytest.LogCaptureFixture
        """
        with caplog.at_level(logging.INFO):
            await LogMailer().send(MESSAGE)
        expect(MESSAGE.to in caplog.text)
        expect(MESSAGE.subject in caplog.text)
        expect(MESSAGE.body in caplog.text)
        assert_expectations()


class TestSmtpMailer:
    """Tests for SmtpMailer.send()."""

    @pytest.mark.parametrize(('login', 'expected_login'), [(SMTP_USER, SMTP_USER), ('', None)], ids=['auth', 'open'])
    @patch(SMTP_SEND, new_callable=AsyncMock)
    async def test_message_goes_to_the_configured_server(
        self, mock_send: AsyncMock, login: str, expected_login: str | None
    ) -> None:
        """Verify the message is addressed from the sender and handed to the server, logging in only with a user.

        :param mock_send: Replacement of ``aiosmtplib.send``, recording what the mailer hands to it.
        :type mock_send: AsyncMock
        :param login: User name and password of the server, empty for an open one.
        :type login: str
        :param expected_login: Credentials ``aiosmtplib.send`` should receive, ``None`` for none.
        :type expected_login: str | None
        """
        server = SmtpServer(host=SMTP_HOST, port=SMTP_PORT, username=login, password=login, sender=SENDER)
        await SmtpMailer(server).send(MESSAGE)
        (email, *_), options = mock_send.await_args_list[0]
        expect((email['From'], email['To'], email['Subject']) == (SENDER, MESSAGE.to, MESSAGE.subject))
        expect(email.get_content().strip() == MESSAGE.body)
        expect((options['hostname'], options['port']) == (SMTP_HOST, SMTP_PORT))
        expect(options['username'] == options['password'] == expected_login)
        assert_expectations()


class TestMailerProvider:
    """Tests for AccountsProvider.mailer()."""

    @pytest.mark.parametrize(('host', 'mailer_class'), [('', LogMailer), (SMTP_HOST, SmtpMailer)], ids=['log', 'smtp'])
    async def test_smtp_host_selects_the_adapter(
        self, fx_settings: Settings, host: str, mailer_class: type[Mailer]
    ) -> None:
        """Verify mail goes over SMTP when a host is configured and to the log otherwise.

        :param fx_settings: Settings of the suite.
        :type fx_settings: Settings
        :param host: SMTP host to configure, empty for none.
        :type host: str
        :param mailer_class: Adapter the container should build for that host.
        :type mailer_class: type[Mailer]
        """
        mail = fx_settings.mail.model_copy(update={'smtp_host': host})
        container = build_container(fx_settings.model_copy(update={'mail': mail}))
        mailer = await container.get(Mailer)
        await container.close()
        assert isinstance(mailer, mailer_class)
