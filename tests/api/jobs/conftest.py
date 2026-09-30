"""Fixtures of the job API tests: the application runs on the job fakes' database, event bus and clock."""

from typing import TYPE_CHECKING

import pytest

from tests.helpers.fakes_jobs import JobFakes, JobFakesProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the application of a job API test runs on.

    :returns: Fakes with an empty database and nothing published.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes) -> Sequence[Provider]:
    """Replace the application's database, event bus and clock with ``fx_fakes``.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :returns: One provider that supplies the fakes.
    :rtype: Sequence[Provider]
    """
    return (JobFakesProvider(fx_fakes),)
