"""Tests for the password hasher, which runs argon2id in worker threads a bounded number at a time."""

import threading
from typing import TYPE_CHECKING, Any, override

import anyio
import pytest
from anyio import from_thread
from delayed_assert import assert_expectations, expect
from fastapi_users.password import PasswordHelper

from bookreviver.app.providers.accounts import PasswordHasher
from tests.harness.fakes.accounts import PASSWORD

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

pytestmark = pytest.mark.anyio

# Long enough for a slow machine to start a worker thread, short enough that a broken limiter fails instead of hanging
GATE_TIMEOUT_S: float = 10.0
HASHED_PASSWORD: str = 'stored-hash'


class GatedPasswordHelper(PasswordHelper):
    """A password helper whose hash and check hold their worker thread until the test opens the gate."""

    def __init__(self, entered: anyio.Event, gate: threading.Event) -> None:
        """Signal ``entered`` when a call starts, and hold every call until ``gate`` is set.

        :param entered: Event of the test's loop, set once the first call has entered the helper.
        :type entered: anyio.Event
        :param gate: Event that releases the held calls.
        :type gate: threading.Event
        """
        super().__init__()
        self._entered = entered
        self._gate = gate

    def _hold(self) -> None:
        """Tell the loop a call has entered, then wait for the gate."""
        from_thread.run_sync(self._entered.set)
        self._gate.wait(GATE_TIMEOUT_S)

    @override
    def hash(self, password: str) -> str:
        """Hold the thread, then return the password itself as its hash.

        :param password: Password to hash.
        :type password: str
        :returns: The password unchanged.
        :rtype: str
        """
        self._hold()
        return password

    @override
    def verify_and_update(self, plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
        """Hold the thread, then report a match that needs no new hash.

        :param plain_password: Password to check.
        :type plain_password: str
        :param hashed_password: Stored hash to check it against.
        :type hashed_password: str
        :returns: A match without an upgraded hash.
        :rtype: tuple[bool, str | None]
        """
        self._hold()
        return True, None


class TestPasswordHasher:
    """Tests for PasswordHasher.hash() and PasswordHasher.verify()."""

    @pytest.mark.parametrize(
        'call',
        [lambda hasher: hasher.hash(PASSWORD), lambda hasher: hasher.verify(PASSWORD, HASHED_PASSWORD)],
        ids=['hash', 'verify'],
    )
    async def test_calls_beyond_the_limit_wait_for_a_free_thread(
        self, call: Callable[[PasswordHasher], Coroutine[Any, Any, object]]
    ) -> None:
        """Verify a second call waits on the hasher's limiter while the first one holds the only token.

        An argon2id hash holds 64 MiB, so without its own limiter a burst of sign-ins would run up to the 40 threads
        of anyio's default limiter at once, and take them from the file work that shares it.

        :param call: The hasher method under test, applied to a hasher.
        :type call: Callable[[PasswordHasher], Coroutine[Any, Any, object]]
        """
        limiter = anyio.CapacityLimiter(1)
        entered = anyio.Event()
        gate = threading.Event()
        hasher = PasswordHasher(GatedPasswordHelper(entered, gate), limiter=limiter)

        with anyio.fail_after(GATE_TIMEOUT_S):
            async with anyio.create_task_group() as group:
                group.start_soon(call, hasher)
                group.start_soon(call, hasher)
                await entered.wait()
                await anyio.wait_all_tasks_blocked()
                statistics = limiter.statistics()
                gate.set()
                expect(statistics.borrowed_tokens == 1)
                expect(statistics.tasks_waiting == 1)
                assert_expectations()
