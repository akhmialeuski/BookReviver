"""Fixtures of the service tests: the local stores over a temporary directory, on top of the root database."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.values import JobEnd
from bookreviver.plugins.split_none import SplitNone
from bookreviver.services.pagination import PaginationService
from tests.helpers.builders import EPOCH
from tests.helpers.clock import FixedClock
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import make_page_service
from tests.helpers.page_stage_writes import PageStageWrites
from tests.helpers.page_writes import PageWrites
from tests.helpers.processing import CV_DEFAULTS, ProcessingKit
from tests.helpers.processors import (
    CleanupProcessor,
    FakeProcessor,
    FirstProcessor,
    MeasuringProcessor,
    SecondProcessor,
    ThirdProcessor,
)
from tests.helpers.samples import CV_MISSING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Job
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.pages import PageService
    from tests.helpers.database import UnitOfWorkFactory


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
def fx_page_stage_writes(monkeypatch: pytest.MonkeyPatch) -> PageStageWrites:
    """Build the record of the writes of the page stage repository, which a test starts after it has seeded.

    :param monkeypatch: Fixture that undoes the patch of the repository after the test.
    :type monkeypatch: pytest.MonkeyPatch
    :returns: Record that holds nothing until ``start`` is called.
    :rtype: PageStageWrites
    """
    return PageStageWrites(monkeypatch)


@pytest.fixture
def fx_page_writes(monkeypatch: pytest.MonkeyPatch) -> PageWrites:
    """Build the record of the pages the page repository writes, which a test starts after it has seeded.

    :param monkeypatch: Fixture that undoes the patch of the repository after the test.
    :type monkeypatch: pytest.MonkeyPatch
    :returns: Record that holds nothing until ``start`` is called.
    :rtype: PageWrites
    """
    return PageWrites(monkeypatch)


@pytest.fixture
def fx_board(
    fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore, fx_events: RecordingEventBus, fx_clock: FixedClock
) -> FakeJobBoard:
    """Build a job board that records what is put on it and runs it when a test says so.

    A ``prepare-pages`` job runs through the page service, as the application runs it, and a job of any other kind
    ends at once, since the page tests look at the queueing of those and not at their work.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_events: Recording event bus.
    :type fx_events: RecordingEventBus
    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :returns: Board the page service puts its jobs on.
    :rtype: FakeJobBoard
    """

    async def run(job: Job, control: JobControl) -> JobEnd:
        """Run a job the way the application would, over a page service of its own.

        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        """
        if job.kind is not JobKind.PREPARE_PAGES:
            return JobEnd(state=JobState.SUCCEEDED)
        service = make_page_service(fx_uow(), fx_asset_store, (fx_events, fx_clock, board))
        return await service.prepare_images(job, control)

    board = FakeJobBoard(runner=run, publisher=fx_events, clock=fx_clock)
    return board


@pytest.fixture
def fx_runtime(
    fx_events: RecordingEventBus, fx_clock: FixedClock, fx_board: FakeJobBoard
) -> tuple[RecordingEventBus, FixedClock, FakeJobBoard]:
    """Gather what a page service reports through: the recording bus, the stopped clock and the job board.

    :param fx_events: Recording event bus.
    :type fx_events: RecordingEventBus
    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :param fx_board: Board that records its jobs.
    :type fx_board: FakeJobBoard
    :returns: The publisher, the clock and the board, as ``make_page_service`` takes them.
    :rtype: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
    """
    return fx_events, fx_clock, fx_board


@pytest.fixture
def fx_service_over(
    fx_asset_store: LocalAssetStore, fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
) -> Callable[[UnitOfWork], PageService]:
    """Return a function building the page service over a unit of work the test made.

    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_runtime: The recording bus, the stopped clock and the job board the service reports through.
    :type fx_runtime: tuple[RecordingEventBus, FixedClock, FakeJobBoard]
    :returns: Function building a service over the given unit of work.
    :rtype: Callable[[UnitOfWork], PageService]
    """
    return lambda uow: make_page_service(uow, fx_asset_store, fx_runtime)


@pytest.fixture
def fx_service(
    fx_uow: UnitOfWorkFactory, fx_service_over: Callable[[UnitOfWork], PageService]
) -> Callable[[], PageService]:
    """Return a function building the page service for one request or job.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_service_over: Function building the page service over a unit of work.
    :type fx_service_over: Callable[[UnitOfWork], PageService]
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], PageService]
    """
    return lambda: fx_service_over(fx_uow())


@pytest.fixture
def fx_pagination_over(fx_clock: FixedClock) -> Callable[[UnitOfWork], PaginationService]:
    """Return a function building the pagination service over a unit of work the test made.

    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :returns: Function building a service over the given unit of work.
    :rtype: Callable[[UnitOfWork], PaginationService]
    """
    return lambda uow: PaginationService(uow=uow, clock=fx_clock)


@pytest.fixture
def fx_pagination(
    fx_uow: UnitOfWorkFactory, fx_pagination_over: Callable[[UnitOfWork], PaginationService]
) -> Callable[[], PaginationService]:
    """Return a function building the pagination service for one request.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_pagination_over: Function building the pagination service over a unit of work.
    :type fx_pagination_over: Callable[[UnitOfWork], PaginationService]
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], PaginationService]
    """
    return lambda: fx_pagination_over(fx_uow())


@pytest.fixture
def fx_kit(fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore) -> ProcessingKit:
    """Build what the processing services of a test share, over the database and the asset store of the test.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The kit that builds the processing service, the jobs of the workers and the edit service.
    :rtype: ProcessingKit
    """
    return ProcessingKit(fx_database, fx_uow, fx_asset_store)


@pytest.fixture
def fx_cv_kit(fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore) -> ProcessingKit:
    """Build the processing kit with the real OpenCV plugins, or skip the test where OpenCV is not installed.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
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
        fx_database,
        fx_uow,
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
def fx_ordered_kit(
    fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore
) -> ProcessingKit:
    """Build the processing kit over processors that declare a place.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
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
    return ProcessingKit(fx_database, fx_uow, fx_asset_store, processors=processors)


@pytest.fixture
def fx_measuring_kit(
    fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore
) -> ProcessingKit:
    """Build the processing kit over a processor that finds an angle on each page.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The kit whose catalogue holds the fakes of the tests and ``geometry.measuring``.
    :rtype: ProcessingKit
    """
    return ProcessingKit(
        fx_database,
        fx_uow,
        fx_asset_store,
        processors=[SplitNone(), FakeProcessor(), CleanupProcessor(), MeasuringProcessor()],
    )
