"""Fixtures of the import API tests: the application runs on the job fakes' database, event bus and clock.

The stores, the imaging adapters and the in-process broker are the application's own, so a test uploads real files
and the import runs to the end in the background, as it does for a user.
"""

from typing import TYPE_CHECKING

import pytest
from taskiq import AsyncBroker, InMemoryBroker

from tests.helpers.builders import EPOCH, make_project
from tests.helpers.fakes_imports import TickingClock
from tests.helpers.fakes_jobs import JobFakes, JobFakesProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import AsyncContainer, Provider

    from bookreviver.domain.entities import Actor, Project


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the application runs on, with a clock that gives every source its own import time.

    :returns: Fakes with an empty database and nothing published.
    :rtype: JobFakes
    """
    return JobFakes(clock=TickingClock(EPOCH))


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes) -> Sequence[Provider]:
    """Replace the application's database, event bus and clock with ``fx_fakes``.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :returns: One provider that supplies the fakes.
    :rtype: Sequence[Provider]
    """
    return (JobFakesProvider(fx_fakes),)


@pytest.fixture
async def fx_project(fx_fakes: JobFakes, fx_actor: Actor) -> Project:
    """Store a project of the signed-in account with no sources.

    :param fx_fakes: Adapters the application runs on.
    :type fx_fakes: JobFakes
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=fx_actor.account_id)
    await fx_fakes.store(project)
    return project


@pytest.fixture
async def fx_broker(fx_container: AsyncContainer) -> InMemoryBroker:
    """Return the broker the application runs its jobs on, which a test waits on until they have finished.

    :param fx_container: Container of the running application.
    :type fx_container: AsyncContainer
    :returns: The started in-process broker, the one the test settings select.
    :rtype: InMemoryBroker
    """
    broker = await fx_container.get(AsyncBroker)
    assert isinstance(broker, InMemoryBroker)
    return broker
