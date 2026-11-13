"""Fixtures of the job API tests: the application runs on the job fakes' event bus and board."""

from typing import TYPE_CHECKING

import pytest

from tests.harness.fakes.jobs import FakeBoardProvider, JobFakesProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from tests.harness.fakes.jobs import JobFakes


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
