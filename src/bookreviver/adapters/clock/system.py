"""Clocks: the system clock, and a fixed one for tests.

Entities record creation and update times from the ``Clock`` port rather than from ``datetime.now``, so a test pins
time with ``FixedClock`` and moves it explicitly instead of sleeping.
"""

from datetime import UTC, datetime
from typing import override

from bookreviver.ports.runtime import Clock


class SystemClock(Clock):
    """The wall clock in UTC."""

    @override
    def now(self) -> datetime:
        """Return the current time in UTC.

        :returns: The current moment with the UTC time zone.
        :rtype: datetime
        """
        return datetime.now(UTC)


class FixedClock(Clock):
    """A clock that shows a set time until moved.

    :ivar moment: Time the clock shows; a test assigns a new value to move it.
    """

    def __init__(self, moment: datetime) -> None:
        """Stop the clock at ``moment``.

        :param moment: Time the clock shows, time-zone aware.
        :type moment: datetime
        """
        self.moment = moment

    @override
    def now(self) -> datetime:
        """Return the set time.

        :returns: The moment the clock is stopped at.
        :rtype: datetime
        """
        return self.moment
