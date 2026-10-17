"""The system clock.

Entities record creation and update times from the ``Clock`` port rather than from ``datetime.now``, so a test pins
time with a fixed clock and moves it explicitly instead of sleeping.
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
