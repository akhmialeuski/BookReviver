"""Fixtures of the job API tests: the application runs on the job fakes' event bus and board."""

from typing import TYPE_CHECKING

import pytest

from tests.helpers.fakes_jobs import FakeBoardProvider, JobFakes, JobFakesProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from tests.helpers.fakes_jobs import RecordingEventBus


@pytest.fixture
def fx_fakes(fx_book_parts: RecordingEventBus) -> JobFakes:
    """Build the adapters the application of a job API test runs on.

    :param fx_book_parts: The recording bus registered as the event channel of every book of the test.
    :type fx_book_parts: RecordingEventBus
    :returns: Fakes with nothing published.
    :rtype: JobFakes
    """
    return JobFakes(events=fx_book_parts)


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes) -> Sequence[Provider]:
    """Replace the application's event bus and job board with ``fx_fakes``.

    The board is the fake one, which runs a job only when a test calls ``run_queued``, so a job a test puts stays
    queued until it says otherwise.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :returns: The providers that supply the fakes.
    :rtype: Sequence[Provider]
    """
    return (JobFakesProvider(fx_fakes), FakeBoardProvider(fx_fakes.board))
