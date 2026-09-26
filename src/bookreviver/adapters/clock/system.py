"""Clocks: the system clock, and a fixed one for tests."""

from datetime import UTC, datetime
from typing import override

from bookreviver.ports.runtime import Clock


class SystemClock(Clock):
    """The wall clock in UTC."""

    @override
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock(Clock):
    """A clock that shows a set time until moved."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    @override
    def now(self) -> datetime:
        return self.moment
