"""Fixtures of the import API tests: the application runs on the job fakes' event bus and clock.

The stores, the imaging adapters and the job board are the application's own, so a test uploads real files and the
import runs to the end in the background, as it does for a user.
"""

from typing import TYPE_CHECKING

import pytest

from tests.api.imports.watch import ImportWatch, JobEndBus
from tests.helpers.builders import EPOCH, make_record
from tests.helpers.fakes_imports import TickingClock
from tests.helpers.fakes_jobs import JobFakes, JobFakesProvider
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider

    from bookreviver.domain.entities import Actor, BookRecord
    from tests.helpers.database import UnitOfWorkFactory


@pytest.fixture
def fx_events() -> JobEndBus:
    """Build the event bus of the application, which tells a test when a job has ended.

    :returns: A bus with nothing published.
    :rtype: JobEndBus
    """
    return JobEndBus()


@pytest.fixture
def fx_fakes(fx_events: JobEndBus) -> JobFakes:
    """Build the adapters the application runs on, with a clock that gives every source its own import time.

    :param fx_events: Event bus of the application.
    :type fx_events: JobEndBus
    :returns: Fakes with nothing published.
    :rtype: JobFakes
    """
    return JobFakes(clock=TickingClock(EPOCH), events=fx_events)


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes) -> Sequence[Provider]:
    """Replace the application's event bus and clock with ``fx_fakes``, and keep its job board.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :returns: One provider that supplies the fakes.
    :rtype: Sequence[Provider]
    """
    return (JobFakesProvider(fx_fakes),)


@pytest.fixture
async def fx_book(fx_uow: UnitOfWorkFactory, fx_actor: Actor) -> BookRecord:
    """Store a project of the signed-in account with no sources.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored project.
    :rtype: BookRecord
    """
    book_record = make_record()
    await store_book(fx_uow(), book_record, owner=fx_actor.account_id)
    return book_record


@pytest.fixture
def fx_import(fx_client: httpx.AsyncClient, fx_events: JobEndBus) -> ImportWatch:
    """Return the reader of the imports the application runs, which a test waits on until they have ended.

    :param fx_client: Client of the signed-in account.
    :type fx_client: httpx.AsyncClient
    :param fx_events: Event bus of the application.
    :type fx_events: JobEndBus
    :returns: The reader of the jobs and of the report of the imports.
    :rtype: ImportWatch
    """
    return ImportWatch(fx_client, fx_events)
