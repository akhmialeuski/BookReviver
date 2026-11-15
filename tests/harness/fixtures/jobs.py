"""The fakes of the jobs a test runs on, besides its databases, which tests of several levels take."""

from typing import TYPE_CHECKING

import pytest

from tests.harness.fakes.jobs import JobFakes

if TYPE_CHECKING:
    from tests.harness.fakes.jobs import RecordingEventBus


@pytest.fixture
def fx_fakes(fx_book_parts: RecordingEventBus) -> JobFakes:
    """Build the adapters a job runs on besides the databases: the board and the events of the books.

    :param fx_book_parts: The recording bus registered as the event channel of every book of the test.
    :type fx_book_parts: RecordingEventBus
    :returns: Fakes with a board with no job and nothing published.
    :rtype: JobFakes
    """
    return JobFakes(events=fx_book_parts)
