"""Fixtures of the account tests: a recording mailer, and a signed-out browser that passes the CSRF check."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.api.auth import current_actor
from tests.helpers.fakes_accounts import (
    CSRF_COOKIE,
    CSRF_HEADER,
    ME_PATH,
    RecordingMailer,
    RecordingMailerProvider,
    Visitor,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider
    from fastapi import FastAPI


@pytest.fixture
def fx_mailer() -> RecordingMailer:
    """Build the mailer that records the messages of one test.

    :returns: A recording mailer with nothing sent.
    :rtype: RecordingMailer
    """
    return RecordingMailer()


@pytest.fixture
def fx_extra_providers(fx_mailer: RecordingMailer) -> Sequence[Provider]:
    """Replace the application's mailer with the recording one.

    :param fx_mailer: Recording mailer of the test.
    :type fx_mailer: RecordingMailer
    :returns: One provider that supplies the recording mailer.
    :rtype: Sequence[Provider]
    """
    return (RecordingMailerProvider(fx_mailer),)


@pytest.fixture
async def fx_browser(fx_app: FastAPI, fx_client: httpx.AsyncClient) -> httpx.AsyncClient:
    """Return a client that is signed out and sends the CSRF header, as the web interface does.

    The suite's application signs in a fixed actor; removing that override leaves the decision to fastapi-users.

    :param fx_app: The running application, whose fixed-actor override is removed.
    :type fx_app: FastAPI
    :param fx_client: In-process client of that application.
    :type fx_client: httpx.AsyncClient
    :returns: The client, holding the CSRF cookie and sending its value in the header.
    :rtype: httpx.AsyncClient
    """
    fx_app.dependency_overrides.pop(current_actor)
    # Any response hands out the CSRF cookie, whose value the client then echoes in the header
    await fx_client.get(ME_PATH)
    fx_client.headers[CSRF_HEADER] = fx_client.cookies[CSRF_COOKIE]
    return fx_client


@pytest.fixture
def fx_visitor(fx_browser: httpx.AsyncClient, fx_mailer: RecordingMailer) -> Visitor:
    """Build a visitor driving the account routes through the browser.

    :param fx_browser: Signed-out client that passes the CSRF check.
    :type fx_browser: httpx.AsyncClient
    :param fx_mailer: Recording mailer the visitor reads tokens from.
    :type fx_mailer: RecordingMailer
    :returns: The visitor.
    :rtype: Visitor
    """
    return Visitor(fx_browser, fx_mailer)
