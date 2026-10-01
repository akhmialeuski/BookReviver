"""Fixtures of the service tests: in-memory persistence and the local stores over a temporary directory."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.services.pages import PageService
from tests.helpers.builders import EPOCH
from tests.helpers.fakes_jobs import RecordingEventBus

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def fx_database() -> InMemoryDatabase:
    """Build an empty in-memory database.

    :returns: Database shared by every unit of work of the test.
    :rtype: InMemoryDatabase
    """
    return InMemoryDatabase()


@pytest.fixture
def fx_storage_root(tmp_path: Path) -> Path:
    """Return the storage root both stores of the test share.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: Directory under the temporary directory, created by the first write.
    :rtype: Path
    """
    return tmp_path / 'storage'


@pytest.fixture
def fx_source_store(fx_storage_root: Path) -> LocalSourceStore:
    """Build the source store over the test's storage root.

    :param fx_storage_root: Storage root shared with the asset store.
    :type fx_storage_root: Path
    :returns: Local source store, the test adapter of the port.
    :rtype: LocalSourceStore
    """
    return LocalSourceStore(root=fx_storage_root)


@pytest.fixture
def fx_asset_store(fx_storage_root: Path) -> LocalAssetStore:
    """Build the asset store over the test's storage root.

    :param fx_storage_root: Storage root shared with the source store.
    :type fx_storage_root: Path
    :returns: Local asset store, the test adapter of the port.
    :rtype: LocalAssetStore
    """
    return LocalAssetStore(root=fx_storage_root)


@pytest.fixture
def fx_events() -> RecordingEventBus:
    """Build an event bus that records everything published.

    :returns: Bus serving as the publisher of every service of the test.
    :rtype: RecordingEventBus
    """
    return RecordingEventBus()


@pytest.fixture
def fx_clock() -> FixedClock:
    """Build a clock stopped at the epoch, which a test moves to tell a change from its absence.

    :returns: Clock stamping what the services change.
    :rtype: FixedClock
    """
    return FixedClock(EPOCH)


@pytest.fixture
def fx_service(
    fx_database: InMemoryDatabase, fx_asset_store: LocalAssetStore, fx_events: RecordingEventBus, fx_clock: FixedClock
) -> Callable[[], PageService]:
    """Return a function building the page service for one request.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_events: Recording event bus the service publishes to.
    :type fx_events: RecordingEventBus
    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], PageService]
    """
    return lambda: PageService(
        uow=InMemoryUnitOfWork(fx_database),
        assets=fx_asset_store,
        order_keys=FractionalOrderKeys(),
        publisher=fx_events,
        clock=fx_clock,
    )
