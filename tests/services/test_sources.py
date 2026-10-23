"""Tests for the source use cases, against the local stores over a temporary directory."""

from typing import TYPE_CHECKING, override
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.enums import JobKind, RejectionReason, Rendition, SourceStatus
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import SourceId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import SliceRequest
from bookreviver.services.sources import SourceService
from tests.helpers.books import SCANS_PER_SOURCE, commit_book, on_disk
from tests.helpers.builders import make_job, make_record, make_source, open_for
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, BookRecord, Source
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from bookreviver.ports.storage import AssetStore, SourceStore
    from tests.helpers.books import Book
    from tests.helpers.clock import FixedClock
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

DUPLICATE_DETAIL: str = 'Repeats part1.pdf.'


class StoreFailedError(OSError):
    """Raised by the source store of a test in place of a real storage failure."""


class SourceStoreFailingOnce(LocalSourceStore):
    """The local source store, whose first source deletion fails before it removes anything."""

    def __init__(self, *, root: Path) -> None:
        """Keep the sources under ``root`` and fail the first deletion.

        :param root: Storage root shared with the asset store.
        :type root: Path
        """
        super().__init__(root=root)
        self._failed = False

    @override
    async def delete_source(self, book_id: BookId, source_id: SourceId) -> None:
        """Fail on the first call, as a storage outage would, and remove the source's files on the later ones.

        :param book_id: Book owning the source.
        :type book_id: BookId
        :param source_id: Source whose files are removed.
        :type source_id: SourceId
        :raises StoreFailedError: On the first call, with nothing removed.
        """
        if not self._failed:
            self._failed = True
            raise StoreFailedError
        await super().delete_source(book_id, source_id)


@pytest.fixture
def fx_import_board(fx_events: RecordingEventBus, fx_clock: FixedClock) -> FakeJobBoard:
    """Build a job board that holds the jobs a test puts on it, and runs them to their end when the test says so.

    :param fx_events: Recording event bus of the test.
    :type fx_events: RecordingEventBus
    :param fx_clock: Clock stopped at the epoch.
    :type fx_clock: FixedClock
    :returns: Board the source service reads the jobs of a book from.
    :rtype: FakeJobBoard
    """
    return FakeJobBoard(runner=ScriptedRunner(), publisher=fx_events, clock=fx_clock)


def _service(uow: UnitOfWork, board: FakeJobBoard, sources: SourceStore, assets: AssetStore) -> SourceService:
    """Build the source service for one request, over a unit of work and the stores and the board of the test.

    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :param board: Board the service reads the jobs of a book from.
    :type board: FakeJobBoard
    :param sources: Source store of the test.
    :type sources: SourceStore
    :param assets: Asset store of the test.
    :type assets: AssetStore
    :returns: The service.
    :rtype: SourceService
    """
    return SourceService(uow=uow, board=board, sources=sources, assets=assets)


@pytest.fixture
def fx_service(
    fx_uow: UnitOfWorkFactory,
    fx_source_store: LocalSourceStore,
    fx_asset_store: LocalAssetStore,
    fx_import_board: FakeJobBoard,
) -> Callable[[], SourceService]:
    """Return a function building the service for one request.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_import_board: Board the service reads the jobs of a book from.
    :type fx_import_board: FakeJobBoard
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], SourceService]
    """
    return lambda: _service(fx_uow(), fx_import_board, fx_source_store, fx_asset_store)


@pytest.fixture
def fx_flaky_service(
    fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore, fx_storage_root: Path, fx_import_board: FakeJobBoard
) -> Callable[[], SourceService]:
    """Return a function building the service for one request over a source store whose first deletion fails.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :param fx_import_board: Board the service reads the jobs of a book from.
    :type fx_import_board: FakeJobBoard
    :returns: Function building a service over a new unit of work and the one failing source store.
    :rtype: Callable[[], SourceService]
    """
    sources = SourceStoreFailingOnce(root=fx_storage_root)
    return lambda: _service(fx_uow(), fx_import_board, sources, fx_asset_store)


async def _record_unimported(uow: UnitOfWork, book_id: BookId) -> tuple[Source, Source]:
    """Record a rejected and a skipped file of a book, after the sources made by ``commit_book``.

    :param uow: Unit of work to commit through.
    :type uow: UnitOfWork
    :param book_id: Book the files were uploaded to.
    :type book_id: BookId
    :returns: The rejected file and the skipped file, in the order they were recorded.
    :rtype: tuple[Source, Source]
    """
    rejected = evolve(
        make_source(name='copy.pdf', minutes=2),
        status=SourceStatus.REJECTED,
        rejection=RejectionReason.DUPLICATE,
        kind=None,
        file_type=None,
        detail=DUPLICATE_DETAIL,
    )
    skipped = evolve(
        make_source(name='skipped.pdf', minutes=3),
        status=SourceStatus.SKIPPED,
        kind=None,
        file_type=None,
    )

    def record(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Add the two files to the book.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the work does not need.
        :type _book_record: BookRecord
        """
        repos.sources.add(rejected)
        repos.sources.add(skipped)

    await uow.change_book(book_id, record)
    return rejected, skipped


@pytest.fixture
async def fx_unimported(fx_uow: UnitOfWorkFactory, fx_book: Book) -> tuple[Source, Source]:
    """Record a rejected and a skipped file in the book of the acting account.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_book: Book of the acting account.
    :type fx_book: Book
    :returns: The rejected file and the skipped file.
    :rtype: tuple[Source, Source]
    """
    return await _record_unimported(fx_uow(), fx_book.book_record.id)


@pytest.fixture
async def fx_book(
    fx_database: SqlDatabase,
    fx_uow: UnitOfWorkFactory,
    fx_source_store: LocalSourceStore,
    fx_asset_store: LocalAssetStore,
    fx_actor: Actor,
) -> Book:
    """Commit and store a book of the acting account.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_actor: Account the service acts for.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_uow(), fx_source_store, fx_asset_store, fx_actor)


@pytest.fixture
async def fx_strangers_book(
    fx_database: SqlDatabase,
    fx_uow: UnitOfWorkFactory,
    fx_source_store: LocalSourceStore,
    fx_asset_store: LocalAssetStore,
) -> Book:
    """Commit and store a book of an account that acts nowhere in the test.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_uow(), fx_source_store, fx_asset_store, None)


class TestList:
    """Tests for SourceService.list()."""

    async def test_lists_the_sources_in_import_order_in_windows(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify the sources come earliest import first, a window holds its part, and the total is the whole list.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        everything = await fx_service().list(open_for(fx_actor, fx_book.book_record.id), SliceRequest())
        window = await fx_service().list(open_for(fx_actor, fx_book.book_record.id), SliceRequest(offset=1, limit=1))

        expect(everything.items == [fx_book.first, fx_book.second])
        expect((window.items, window.total) == ([fx_book.second], 2))
        assert_expectations()

    async def test_lists_the_imported_sources_only_unless_the_other_files_are_asked_for(
        self,
        fx_service: Callable[[], SourceService],
        fx_book: Book,
        fx_unimported: tuple[Source, Source],
        fx_actor: Actor,
    ) -> None:
        """Verify the default is the imported sources, and the rejected and skipped files come when their statuses are asked.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_unimported: The rejected file and the skipped file recorded in the book.
        :type fx_unimported: tuple[Source, Source]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        rejected, skipped = fx_unimported

        imported = await fx_service().list(open_for(fx_actor, fx_book.book_record.id), SliceRequest())
        report = await fx_service().list(
            open_for(fx_actor, fx_book.book_record.id),
            SliceRequest(),
            statuses=(SourceStatus.REJECTED, SourceStatus.SKIPPED),
        )
        everything = await fx_service().list(
            open_for(fx_actor, fx_book.book_record.id), SliceRequest(), statuses=tuple(SourceStatus)
        )

        expect(imported.items == [fx_book.first, fx_book.second])
        expect((report.items, report.total) == ([rejected, skipped], 2))
        expect(everything.items == [fx_book.first, fx_book.second, rejected, skipped])
        assert_expectations()


class TestGet:
    """Tests for SourceService.get()."""

    async def test_returns_the_source_of_the_book(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify a source is read by its identifier through its book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        assert await fx_service().get(open_for(fx_actor, fx_book.book_record.id), fx_book.second.id) == fx_book.second

    async def test_source_of_another_book_of_the_actor_is_not_found(
        self, fx_service: Callable[[], SourceService], fx_uow: UnitOfWorkFactory, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify a source is found only through the book holding it, even for an owner of both books.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        other = make_record()
        await store_book(fx_uow(), other, owner=fx_actor.account_id)

        with pytest.raises(NotFoundError):
            await fx_service().get(open_for(fx_actor, other.id), fx_book.first.id)

    async def test_missing_source_and_a_source_of_another_book_are_not_found(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_strangers_book: Book, fx_actor: Actor
    ) -> None:
        """Verify an identifier no source has and a source of another book answer alike.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_strangers_book: Another book, whose source the acting account's book does not have.
        :type fx_strangers_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        for source_id in (SourceId(uuid4()), fx_strangers_book.first.id):
            with pytest.raises(NotFoundError):
                await fx_service().get(open_for(fx_actor, fx_book.book_record.id), source_id)


class TestScans:
    """Tests for SourceService.scans()."""

    async def test_lists_the_scans_of_the_book_source_by_source(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify the scans of every source come in import order and by number, with the whole count in the total.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        scans = await fx_service().scans(open_for(fx_actor, fx_book.book_record.id), SliceRequest())

        expect(scans.items == fx_book.scans)
        expect(scans.total == len(fx_book.scans))
        assert_expectations()

    async def test_lists_the_scans_of_one_source_in_windows(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify a source's scans are cut into windows, with the count of that source in the total.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        window = await fx_service().scans(
            open_for(fx_actor, fx_book.book_record.id), SliceRequest(offset=1, limit=1), source_id=fx_book.second.id
        )

        expect(window.items == [fx_book.scans[SCANS_PER_SOURCE + 1]])
        expect(window.total == SCANS_PER_SOURCE)
        assert_expectations()

    async def test_source_of_another_book_is_not_found(
        self, fx_service: Callable[[], SourceService], fx_uow: UnitOfWorkFactory, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify the scans of a source are listed only through the book holding it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        other = make_record()
        await store_book(fx_uow(), other, owner=fx_actor.account_id)

        with pytest.raises(NotFoundError):
            await fx_service().scans(open_for(fx_actor, other.id), SliceRequest(), source_id=fx_book.first.id)


class TestDelete:
    """Tests for SourceService.delete()."""

    async def test_removes_the_source_its_scans_and_their_files_but_keeps_the_page(
        self,
        fx_service: Callable[[], SourceService],
        fx_uow: UnitOfWorkFactory,
        fx_book: Book,
        fx_storage_root: Path,
        fx_actor: Actor,
    ) -> None:
        """Verify the source, its scans and their files go, the other source stays, and the page keeps its image.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        await fx_service().delete(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)

        uow = fx_uow()
        remaining = await fx_service().list(open_for(fx_actor, fx_book.book_record.id), SliceRequest())
        scans = await fx_service().scans(open_for(fx_actor, fx_book.book_record.id), SliceRequest())
        image = BookKeys(fx_book.book_record.id).version_rendition(fx_book.version, Rendition.FULL_JPEG)
        page = await uow.read_book(fx_book.book_record.id, lambda repos: repos.pages.get(fx_book.page.id))
        versions = await uow.read_book(
            fx_book.book_record.id, lambda repos: repos.page_versions.list_for_page(fx_book.page.id)
        )
        expect(remaining.items == [fx_book.second])
        expect(scans.items == fx_book.scans[SCANS_PER_SOURCE:])
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (False, False))
        expect(on_disk(fx_storage_root, fx_book, fx_book.second) == (True, True))
        expect(page == evolve(fx_book.page, scan_id=None))
        expect(versions == [fx_book.version])
        expect((fx_storage_root / image).is_file())
        assert_expectations()

    async def test_import_in_progress_is_a_conflict_and_removes_nothing(
        self,
        fx_service: Callable[[], SourceService],
        fx_import_board: FakeJobBoard,
        fx_book: Book,
        fx_storage_root: Path,
        fx_actor: Actor,
    ) -> None:
        """Verify a source cannot be deleted while the book imports, whose job may be writing its scans.

        A queued job is active, as a running one is.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_import_board: Board the service reads the jobs of a book from.
        :type fx_import_board: FakeJobBoard
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        fx_import_board.put(make_job(book_id=fx_book.book_record.id, kind=JobKind.IMPORT_SOURCE))

        with pytest.raises(ConflictError):
            await fx_service().delete(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)

        expect(await fx_service().get(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id) == fx_book.first)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (True, True))
        assert_expectations()

    async def test_finished_import_does_not_block_the_deletion(
        self, fx_service: Callable[[], SourceService], fx_import_board: FakeJobBoard, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify an import that has ended leaves the book free to delete the source, by hand.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_import_board: Board the service reads the jobs of a book from.
        :type fx_import_board: FakeJobBoard
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        fx_import_board.put(make_job(book_id=fx_book.book_record.id, kind=JobKind.IMPORT_SOURCE))
        await fx_import_board.run_queued()

        await fx_service().delete(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)

        with pytest.raises(NotFoundError):
            await fx_service().get(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)

    async def test_failed_deletion_keeps_the_source_and_a_repeat_finishes_it(
        self, fx_flaky_service: Callable[[], SourceService], fx_book: Book, fx_storage_root: Path, fx_actor: Actor
    ) -> None:
        """Verify a storage failure leaves the source reachable, so deleting again removes what is left.

        :param fx_flaky_service: Function building the service over a source store whose first deletion fails.
        :type fx_flaky_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        with pytest.raises(StoreFailedError):
            await fx_flaky_service().delete(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)
        kept = await fx_flaky_service().get(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)
        await fx_flaky_service().delete(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)

        expect(kept == fx_book.first)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (False, False))
        with pytest.raises(NotFoundError):
            await fx_flaky_service().get(open_for(fx_actor, fx_book.book_record.id), fx_book.first.id)
        assert_expectations()


class TestDeleteUnimported:
    """Tests for SourceService.delete_unimported()."""

    @pytest.mark.usefixtures('fx_unimported')
    async def test_removes_the_rejected_and_skipped_files_and_keeps_the_imported_sources(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_storage_root: Path, fx_actor: Actor
    ) -> None:
        """Verify the report of an import is closed, and the sources of the book, their scans and files stay.

        The rejected and the skipped file are recorded in the book by the ``fx_unimported`` fixture.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        await fx_service().delete_unimported(open_for(fx_actor, fx_book.book_record.id))

        everything = await fx_service().list(
            open_for(fx_actor, fx_book.book_record.id), SliceRequest(), statuses=tuple(SourceStatus)
        )
        scans = await fx_service().scans(open_for(fx_actor, fx_book.book_record.id), SliceRequest())
        expect(everything.items == [fx_book.first, fx_book.second])
        expect(scans.items == fx_book.scans)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (True, True))
        assert_expectations()

    async def test_import_in_progress_is_a_conflict_and_keeps_the_report(
        self,
        fx_service: Callable[[], SourceService],
        fx_import_board: FakeJobBoard,
        fx_book: Book,
        fx_unimported: tuple[Source, Source],
        fx_actor: Actor,
    ) -> None:
        """Verify the report is not closed while the book imports, since the import is writing it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_import_board: Board the service reads the jobs of a book from.
        :type fx_import_board: FakeJobBoard
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_unimported: The rejected file and the skipped file recorded in the book.
        :type fx_unimported: tuple[Source, Source]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        fx_import_board.put(make_job(book_id=fx_book.book_record.id, kind=JobKind.IMPORT_SOURCE))

        with pytest.raises(ConflictError):
            await fx_service().delete_unimported(open_for(fx_actor, fx_book.book_record.id))

        report = await fx_service().list(
            open_for(fx_actor, fx_book.book_record.id),
            SliceRequest(),
            statuses=(SourceStatus.REJECTED, SourceStatus.SKIPPED),
        )
        assert report.items == list(fx_unimported)
