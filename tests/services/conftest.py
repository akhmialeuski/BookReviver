"""Fixtures of the service tests: in-memory persistence and the local stores over a temporary directory."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.plugins.split_none import SplitNone
from bookreviver.services.pagination import PaginationService
from tests.helpers.builders import EPOCH
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import make_page_service
from tests.helpers.processing import CV_DEFAULTS, ProcessingKit
from tests.helpers.processors import (
    CleanupProcessor,
    FakeProcessor,
    FirstProcessor,
    SecondProcessor,
    ThirdProcessor,
)
from tests.helpers.samples import CV_MISSING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.services.pages import PageService


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
def fx_queue() -> RecordingJobQueue:
    """Build a job queue that records what is enqueued and runs nothing.

    :returns: Queue the page service hands its jobs to.
    :rtype: RecordingJobQueue
    """
    return RecordingJobQueue()


@pytest.fixture
def fx_runtime(
    fx_events: RecordingEventBus, fx_clock: FixedClock, fx_queue: RecordingJobQueue
) -> tuple[RecordingEventBus, FixedClock, RecordingJobQueue]:
    """Gather what a page service reports through: the recording bus, the stopped clock and the recording queue.

    :param fx_events: Recording event bus.
    :type fx_events: RecordingEventBus
    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :param fx_queue: Recording job queue.
    :type fx_queue: RecordingJobQueue
    :returns: The publisher, the clock and the queue, as ``make_page_service`` takes them.
    :rtype: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
    """
    return fx_events, fx_clock, fx_queue


@pytest.fixture
def fx_service(
    fx_database: InMemoryDatabase,
    fx_asset_store: LocalAssetStore,
    fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue],
) -> Callable[[], PageService]:
    """Return a function building the page service for one request or job.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_runtime: The recording bus, the stopped clock and the recording queue the service reports through.
    :type fx_runtime: tuple[RecordingEventBus, FixedClock, RecordingJobQueue]
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], PageService]
    """
    return lambda: make_page_service(InMemoryUnitOfWork(fx_database), fx_asset_store, fx_runtime)


@pytest.fixture
def fx_pagination(
    fx_database: InMemoryDatabase, fx_events: RecordingEventBus, fx_clock: FixedClock
) -> Callable[[], PaginationService]:
    """Return a function building the pagination service for one request.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_events: Recording event bus the service publishes to.
    :type fx_events: RecordingEventBus
    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], PaginationService]
    """
    return lambda: PaginationService(uow=InMemoryUnitOfWork(fx_database), publisher=fx_events, clock=fx_clock)


@pytest.fixture
def fx_kit(fx_asset_store: LocalAssetStore) -> ProcessingKit:
    """Build what the processing services of a test share, over the test's asset store.

    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The kit that builds the processing service, the jobs of the workers and the edit service.
    :rtype: ProcessingKit
    """
    return ProcessingKit(fx_asset_store)


@pytest.fixture
def fx_cv_kit(fx_asset_store: LocalAssetStore) -> ProcessingKit:
    """Build the processing kit with the real OpenCV plugins, or skip the test where OpenCV is not installed.

    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The kit whose catalogue has ``split.none``, ``split.spread``, ``split.auto``, the five geometry
              processors and the four cleanup processors, which the default recipes of those stages need.
    :rtype: ProcessingKit
    """
    split = pytest.importorskip('bookreviver.plugins.split_spread', reason=CV_MISSING).SplitSpread()
    auto = pytest.importorskip('bookreviver.plugins.split_auto', reason=CV_MISSING).SplitAuto()
    perspective = pytest.importorskip('bookreviver.plugins.perspective', reason=CV_MISSING).Perspective()
    deskew = pytest.importorskip('bookreviver.plugins.deskew', reason=CV_MISSING).Deskew()
    dewarp = pytest.importorskip('bookreviver.plugins.dewarp', reason=CV_MISSING).Dewarp()
    crop = pytest.importorskip('bookreviver.plugins.crop', reason=CV_MISSING).Crop()
    normalize = pytest.importorskip('bookreviver.plugins.normalize', reason=CV_MISSING).Normalize()
    binarize = pytest.importorskip('bookreviver.plugins.binarize', reason=CV_MISSING).Binarize()
    despeckle = pytest.importorskip('bookreviver.plugins.despeckle', reason=CV_MISSING).Despeckle()
    thickness = pytest.importorskip('bookreviver.plugins.thickness', reason=CV_MISSING).Thickness()
    eraser = pytest.importorskip('bookreviver.plugins.eraser', reason=CV_MISSING).Eraser()
    return ProcessingKit(
        fx_asset_store,
        processors=[
            SplitNone(),
            split,
            auto,
            perspective,
            deskew,
            dewarp,
            crop,
            normalize,
            binarize,
            despeckle,
            thickness,
            eraser,
        ],
        defaults=CV_DEFAULTS,
    )


@pytest.fixture
def fx_ordered_kit(fx_asset_store: LocalAssetStore) -> ProcessingKit:
    """Build the processing kit over processors that declare a place.

    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The kit whose catalogue holds the fakes of the tests and the processors that ask for a place.
    :rtype: ProcessingKit
    """
    processors = [
        SplitNone(),
        FakeProcessor(),
        CleanupProcessor(),
        FirstProcessor(),
        SecondProcessor(),
        ThirdProcessor(),
    ]
    return ProcessingKit(fx_asset_store, processors=processors)
