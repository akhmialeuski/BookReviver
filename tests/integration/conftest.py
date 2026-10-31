"""Fixtures of the integration tests: the bus the changes of the books of a test are recorded on."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.harness.fakes.jobs import RecordingEventBus


@pytest.fixture
def fx_book_changes(fx_book_parts: RecordingEventBus) -> RecordingEventBus:
    """Return the bus the changes of the books that the test announces are recorded on.

    :param fx_book_parts: The bus registered for the test.
    :type fx_book_parts: RecordingEventBus
    :returns: The bus every book database of the test publishes to, which is the bus of the services too.
    :rtype: RecordingEventBus
    """
    return fx_book_parts
