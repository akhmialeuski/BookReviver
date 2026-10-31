"""Tests for the loop that deletes the sessions that outlived their lifetime, with the sleep between its runs replaced."""

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccessTokenDatabase
from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.providers import accounts as accounts_module
from bookreviver.app.providers.accounts import SessionCleanup
from tests.harness.builders.seeding import commit_account

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

EXPIRED_TOKEN: str = 'e' * 43
LATER_EXPIRED_TOKEN: str = 'f' * 43
LIVE_TOKEN: str = 'l' * 43
# How far from the end of the session lifetime a token is created, to keep the test clear of the clock
MARGIN: timedelta = timedelta(hours=1)
# The name the loop looks its sleep up by in the module of the cleanup
SLEEP: str = 'sleep'
# What the failing run says
BUSY: str = 'the writer is busy'
# The interval a test gives the loop in place of the one of the settings
INTERVAL_SECONDS: float = 7


class StopLoop(Exception):
    """Ends the loop under test, which has no end of its own."""


class FakeSleep:
    """Stands in for the sleep between two runs of the loop: records the interval and never waits.

    :ivar intervals: Intervals the loop slept for.
    """

    def __init__(self, *, runs: int, at_first_sleep: Callable[[], Awaitable[None]] | None = None) -> None:
        """Let the loop sleep ``runs`` times, and end it at the last sleep.

        :param runs: How many runs the loop makes before it is ended.
        :type runs: int
        :param at_first_sleep: What happens while the loop sleeps for the first time, which changes what the next run
            finds, or None for nothing.
        :type at_first_sleep: Callable[[], Awaitable[None]] | None
        """
        self.intervals: list[float] = []
        self._runs = runs
        self._at_first_sleep = at_first_sleep

    async def __call__(self, seconds: float) -> None:
        """Record the interval, and raise ``StopLoop`` after the last run.

        :param seconds: The interval the loop asks to sleep for.
        :type seconds: float
        :raises StopLoop: At the sleep that follows the last run.
        """
        if self._at_first_sleep is not None and not self.intervals:
            await self._at_first_sleep()
        self.intervals.append(seconds)
        if len(self.intervals) >= self._runs:
            raise StopLoop


async def _store(container: AsyncContainer, owner: AccountId, token: str, created_at: datetime) -> None:
    """Store a session token of ``owner``, created at ``created_at``.

    :param container: The application's container.
    :type container: AsyncContainer
    :param owner: The account the session belongs to.
    :type owner: AccountId
    :param token: Value of the token.
    :type token: str
    :param created_at: When the session was created.
    :type created_at: datetime
    """
    async with container() as scope:
        await (await scope.get(AccessTokenDatabase)).create(
            {'token': token, 'user_id': owner, 'created_at': created_at}
        )


async def _stored(container: AsyncContainer, *tokens: str) -> list[str]:
    """Return which of the tokens are stored.

    :param container: The application's container.
    :type container: AsyncContainer
    :param tokens: Values of the tokens to look for.
    :type tokens: str
    :returns: The values that are stored, in the order given.
    :rtype: list[str]
    """
    async with container() as scope:
        database = await scope.get(AccessTokenDatabase)
        return [token for token in tokens if await database.get_by_token(token) is not None]


class TestSessionCleanup:
    """Tests for SessionCleanup, which the application runs in its task group."""

    async def test_purge_deletes_the_sessions_older_than_the_lifetime(
        self, fx_container: AsyncContainer, fx_settings: Settings
    ) -> None:
        """Verify a run keeps a session still inside its lifetime and deletes one past it.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        :param fx_settings: Settings of the test, which give the session lifetime.
        :type fx_settings: Settings
        """
        owner = await commit_account(await fx_container.get(SqlDatabase))
        lifetime = timedelta(seconds=fx_settings.auth.session_lifetime_seconds)
        await _store(fx_container, owner, EXPIRED_TOKEN, datetime.now(UTC) - lifetime - MARGIN)
        await _store(fx_container, owner, LIVE_TOKEN, datetime.now(UTC) - lifetime + MARGIN)

        await (await fx_container.get(SessionCleanup)).purge()

        assert await _stored(fx_container, EXPIRED_TOKEN, LIVE_TOKEN) == [LIVE_TOKEN]

    async def test_loop_purges_at_the_start_and_after_every_interval(
        self, fx_container: AsyncContainer, fx_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the loop purges before its first sleep and again after each sleep, for the configured interval.

        The second session expires while the loop sleeps for the first time, so only the second run can delete it.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        :param fx_settings: Settings of the test, which give the interval and the session lifetime.
        :type fx_settings: Settings
        :param monkeypatch: Replaces the sleep of the loop.
        :type monkeypatch: pytest.MonkeyPatch
        """
        owner = await commit_account(await fx_container.get(SqlDatabase))
        expired_at = datetime.now(UTC) - timedelta(seconds=fx_settings.auth.session_lifetime_seconds) - MARGIN
        await _store(fx_container, owner, EXPIRED_TOKEN, expired_at)
        await _store(fx_container, owner, LIVE_TOKEN, datetime.now(UTC))
        seen_after_first_run: list[str] = []

        async def expire_one() -> None:
            """Record what the first run left, then store a session that is already past its lifetime."""
            seen_after_first_run.extend(await _stored(fx_container, EXPIRED_TOKEN, LIVE_TOKEN))
            await _store(fx_container, owner, LATER_EXPIRED_TOKEN, expired_at)

        sleep = FakeSleep(runs=2, at_first_sleep=expire_one)
        monkeypatch.setattr(accounts_module, SLEEP, sleep)

        with pytest.raises(StopLoop):
            await (await fx_container.get(SessionCleanup)).run()

        expect(seen_after_first_run == [LIVE_TOKEN])
        expect(await _stored(fx_container, EXPIRED_TOKEN, LATER_EXPIRED_TOKEN, LIVE_TOKEN) == [LIVE_TOKEN])
        expect(sleep.intervals == [fx_settings.auth.session_cleanup_interval_seconds] * 2)
        assert_expectations()

    async def test_a_failed_run_is_logged_and_the_next_one_still_happens(
        self, fx_container: AsyncContainer, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify a run that fails does not end the loop, which sleeps the given interval and runs again.

        :param fx_container: The application's container.
        :type fx_container: AsyncContainer
        :param monkeypatch: Replaces the sleep of the loop and the purge.
        :type monkeypatch: pytest.MonkeyPatch
        :param caplog: Captures what the loop logs.
        :type caplog: pytest.LogCaptureFixture
        """
        purge = AsyncMock(side_effect=[RuntimeError(BUSY), None])
        sleep = FakeSleep(runs=2)
        monkeypatch.setattr(accounts_module, SLEEP, sleep)
        monkeypatch.setattr(SessionCleanup, 'purge', purge)
        cleanup = SessionCleanup(fx_container, interval_seconds=INTERVAL_SECONDS, lifetime_seconds=1)

        with caplog.at_level(logging.ERROR, logger=accounts_module.__name__), pytest.raises(StopLoop):
            await cleanup.run()

        expect(purge.await_count == len(sleep.intervals))
        expect(sleep.intervals == [INTERVAL_SECONDS] * 2)
        expect('could not be deleted' in caplog.text)
        assert_expectations()
