"""Tests for the start of the session cleanup by the lifespan of the application."""

from typing import TYPE_CHECKING

import pytest
from anyio import wait_all_tasks_blocked
from dishka import Provider, Scope

from bookreviver.app.providers.accounts import SessionCleanup

if TYPE_CHECKING:
    from collections.abc import Sequence


pytestmark = pytest.mark.anyio


class RecordingCleanup:
    """Stands in for the session cleanup: its loop counts each start and returns at once.

    A plain method rather than a mock, because the task group names a task after the ``__qualname__`` of its function.

    :ivar runs: How many times the loop was started.
    """

    def __init__(self) -> None:
        """Start with no run counted."""
        self.runs = 0

    async def run(self) -> None:
        """Count one start of the loop."""
        self.runs += 1


@pytest.fixture
def fx_cleanup() -> RecordingCleanup:
    """Return a stand-in for the session cleanup, whose loop returns at once and counts that it ran.

    :returns: The recording stand-in.
    :rtype: RecordingCleanup
    """
    return RecordingCleanup()


@pytest.fixture
def fx_extra_providers(fx_cleanup: RecordingCleanup) -> Sequence[Provider]:
    """Give the running application the stand-in cleanup in place of its own.

    :param fx_cleanup: The stand-in for the session cleanup.
    :type fx_cleanup: RecordingCleanup
    :returns: The provider of the stand-in.
    :rtype: Sequence[Provider]
    """
    provider = Provider(scope=Scope.APP)
    provider.provide(lambda: fx_cleanup, provides=SessionCleanup, override=True)
    return (provider,)


class TestSessionCleanupInLifespan:
    """Tests for the start of the session cleanup by the lifespan of the application."""

    @pytest.mark.usefixtures('fx_app')
    async def test_lifespan_runs_the_cleanup_of_the_container(self, fx_cleanup: RecordingCleanup) -> None:
        """Verify the running application has started the loop of the cleanup its container builds.

        The application is only started and no request is made, so the run can only come from its task group.

        :param fx_cleanup: The stand-in for the session cleanup, which the container of the application returns.
        :type fx_cleanup: RecordingCleanup
        """
        await wait_all_tasks_blocked()

        assert fx_cleanup.runs == 1
