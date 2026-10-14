"""Tests that the account routes which hash or check a password leave the event loop serving, read by the lag probe.

The probe of the application runs in a task group of the test, around the requests of one operation, and records how
late the event loop woke each of its ticks. An argon2id hash or check on the loop holds it for 38 to 45 ms, so a tick
asleep across it ends that late. In a worker thread the requests delay the loop about as much as a request that hashes
nothing, a few milliseconds at most, which is the noise of the interpreter switching threads every 5 ms.

The first request that reaches the ORM in a process holds the loop for 10 to 29 ms while SQLAlchemy configures its
mappers and the request paths run cold, so each test signs up and signs out one account before it measures.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.app.providers.accounts import AccountMail
from bookreviver.app.tracing import LoopLagProbe
from bookreviver.domain.enums import SpanAttribute, SpanName
from tests.helpers.fakes_accounts import (
    EMAIL_FIELD,
    FORGOT_PATH,
    LOGOUT_PATH,
    ME_PATH,
    PASSWORD_FIELD,
    RESET_PATH,
    TOKEN_PARAMETER,
)
from tests.helpers.tracers import AskingTracer

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

    from tests.helpers.fakes_accounts import RecordingMailer, Visitor

pytestmark = pytest.mark.anyio

EMAIL: str = 'reader@example.org'
WARM_UP_EMAIL: str = 'warm-up@example.org'
NEW_PASSWORD: str = 'a completely different phrase'
# The resolution of the probe, well below the bound it checks
PROBE_INTERVAL_S: float = 0.001
# Under half of one argon2id hash or check on the loop, and about six times the 3.4 ms that a request without a hash
# reached in ten repeats
MAX_LAG_MS: float = 20.0


@pytest.fixture
async def fx_warm_visitor(fx_visitor: Visitor) -> Visitor:
    """Return the visitor after one account has signed up and signed out, so the ORM and the routes are warm.

    :param fx_visitor: Signed-out visitor of the application.
    :type fx_visitor: Visitor
    :returns: The same visitor, signed out again.
    :rtype: Visitor
    """
    await fx_visitor.sign_up(WARM_UP_EMAIL)
    await fx_visitor.client.post(LOGOUT_PATH)
    return fx_visitor


async def lags_during(operation: Callable[[], Awaitable[httpx.Response]]) -> tuple[httpx.Response, list[float]]:
    """Run ``operation`` while the lag probe ticks, and return its response with the lag of every tick it spanned.

    The operation starts once the first tick is asleep. After it, the test waits for one more tick to start, which
    means the tick asleep when the operation ended has ended too and holds its lag.

    :param operation: The requests whose effect on the event loop is measured.
    :type operation: Callable[[], Awaitable[httpx.Response]]
    :returns: The response of the operation, and the lag of each tick in milliseconds.
    :rtype: tuple[httpx.Response, list[float]]
    """
    tracer = AskingTracer()
    probe = LoopLagProbe(tracer=tracer, interval_seconds=PROBE_INTERVAL_S)
    asleep = tracer.ask_after(1, name=SpanName.LOOP_LAG)
    responses: list[httpx.Response] = []
    async with anyio.create_task_group() as group:
        group.start_soon(probe.run)
        await asleep.wait()
        responses.append(await operation())
        ended = tracer.ask_after(1, name=SpanName.LOOP_LAG)
        await ended.wait()
        group.cancel_scope.cancel()
    # The tick that started last was still asleep when the probe was cancelled, so it carries no lag
    ticks = tracer.named(SpanName.LOOP_LAG)
    lags = [float(tick.attributes[SpanAttribute.LAG_MS]) for tick in ticks if SpanAttribute.LAG_MS in tick.attributes]
    return responses[0], lags


def expect_on_time(response: httpx.Response, status: HTTPStatus, lags: list[float]) -> None:
    """Check the operation succeeded and no tick of the probe it spanned was late beyond the bound.

    :param response: Response of the operation.
    :type response: httpx.Response
    :param status: Status the operation answers with when it succeeds.
    :type status: HTTPStatus
    :param lags: Lag of each tick in milliseconds.
    :type lags: list[float]
    """
    expect(response.status_code == status)
    expect(bool(lags), 'No tick was recorded.')
    expect(max(lags, default=0.0) <= MAX_LAG_MS, f'The lags were {lags}.')
    assert_expectations()


class TestHashingLeavesTheLoopOnTime:
    """Tests that registration, sign-in, password reset and a password change never hold the event loop for a hash."""

    async def test_registration(self, fx_warm_visitor: Visitor) -> None:
        """Verify registering an account, which hashes its password, delays no tick of the probe beyond the bound.

        :param fx_warm_visitor: Signed-out visitor of a warm application.
        :type fx_warm_visitor: Visitor
        """
        response, lags = await lags_during(lambda: fx_warm_visitor.register(EMAIL))
        expect_on_time(response, HTTPStatus.CREATED, lags)

    async def test_sign_in(self, fx_warm_visitor: Visitor) -> None:
        """Verify signing in, which checks the password, delays no tick of the probe beyond the bound.

        :param fx_warm_visitor: Signed-out visitor of a warm application.
        :type fx_warm_visitor: Visitor
        """
        response, lags = await lags_during(lambda: fx_warm_visitor.login(WARM_UP_EMAIL))
        expect_on_time(response, HTTPStatus.NO_CONTENT, lags)

    async def test_password_reset(self, fx_warm_visitor: Visitor, fx_mailer: RecordingMailer) -> None:
        """Verify asking for a reset and setting the new password, which hash three times, delay no tick beyond the bound.

        :param fx_warm_visitor: Signed-out visitor of a warm application.
        :type fx_warm_visitor: Visitor
        :param fx_mailer: Recording mailer the reset token is read from.
        :type fx_mailer: RecordingMailer
        """

        async def reset() -> httpx.Response:
            """Ask for the reset mail, then set the new password with the token it carries.

            :returns: Response of the reset route.
            :rtype: httpx.Response
            """
            await fx_warm_visitor.client.post(FORGOT_PATH, json={EMAIL_FIELD: WARM_UP_EMAIL})
            token = fx_mailer.token(AccountMail.RESET.subject)
            return await fx_warm_visitor.client.post(
                RESET_PATH, json={TOKEN_PARAMETER: token, PASSWORD_FIELD: NEW_PASSWORD}
            )

        response, lags = await lags_during(reset)
        expect_on_time(response, HTTPStatus.OK, lags)

    async def test_password_change(self, fx_warm_visitor: Visitor) -> None:
        """Verify changing the password of the signed-in account delays no tick of the probe beyond the bound.

        :param fx_warm_visitor: Signed-out visitor of a warm application.
        :type fx_warm_visitor: Visitor
        """
        await fx_warm_visitor.login(WARM_UP_EMAIL)
        response, lags = await lags_during(
            lambda: fx_warm_visitor.client.patch(ME_PATH, json={PASSWORD_FIELD: NEW_PASSWORD})
        )
        expect_on_time(response, HTTPStatus.OK, lags)
