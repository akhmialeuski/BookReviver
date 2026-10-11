"""Tests for the source use cases, against in-memory persistence and the local stores over a temporary directory."""

from typing import TYPE_CHECKING, override
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.enums import JobState, Rendition
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import SourceId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import SliceRequest
from bookreviver.services.sources import SourceService
from tests.helpers.books import SCANS_PER_SOURCE, commit_book, on_disk
from tests.helpers.builders import make_job, make_project
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import ProjectId
    from tests.helpers.books import Book

pytestmark = pytest.mark.anyio


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
    async def delete_source(self, project_id: ProjectId, source_id: SourceId) -> None:
        """Fail on the first call, as a storage outage would, and remove the source's files on the later ones.

        :param project_id: Project owning the source.
        :type project_id: ProjectId
        :param source_id: Source whose files are removed.
        :type source_id: SourceId
        :raises StoreFailedError: On the first call, with nothing removed.
        """
        if not self._failed:
            self._failed = True
            raise StoreFailedError
        await super().delete_source(project_id, source_id)


@pytest.fixture
def fx_service(
    fx_database: InMemoryDatabase, fx_source_store: LocalSourceStore, fx_asset_store: LocalAssetStore
) -> Callable[[], SourceService]:
    """Return a function building the service for one request.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], SourceService]
    """
    return lambda: SourceService(uow=InMemoryUnitOfWork(fx_database), sources=fx_source_store, assets=fx_asset_store)


@pytest.fixture
def fx_flaky_service(
    fx_database: InMemoryDatabase, fx_asset_store: LocalAssetStore, fx_storage_root: Path
) -> Callable[[], SourceService]:
    """Return a function building the service for one request over a source store whose first deletion fails.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :returns: Function building a service over a new unit of work and the one failing source store.
    :rtype: Callable[[], SourceService]
    """
    sources = SourceStoreFailingOnce(root=fx_storage_root)
    return lambda: SourceService(uow=InMemoryUnitOfWork(fx_database), sources=sources, assets=fx_asset_store)


@pytest.fixture
async def fx_book(
    fx_database: InMemoryDatabase, fx_source_store: LocalSourceStore, fx_asset_store: LocalAssetStore, fx_actor: Actor
) -> Book:
    """Commit and store a book of the acting account.

    :param fx_database: In-memory database of the test.
    :type fx_database: InMemoryDatabase
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_actor: Account the service acts for.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_source_store, fx_asset_store, fx_actor)


@pytest.fixture
async def fx_strangers_book(
    fx_database: InMemoryDatabase, fx_source_store: LocalSourceStore, fx_asset_store: LocalAssetStore
) -> Book:
    """Commit and store a book of an account that acts nowhere in the test.

    :param fx_database: In-memory database of the test.
    :type fx_database: InMemoryDatabase
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_source_store, fx_asset_store, None)


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
        everything = await fx_service().list(fx_actor, fx_book.project.id, SliceRequest())
        window = await fx_service().list(fx_actor, fx_book.project.id, SliceRequest(offset=1, limit=1))

        expect(everything.items == [fx_book.first, fx_book.second])
        expect((window.items, window.total) == ([fx_book.second], 2))
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], SourceService], fx_strangers_book: Book, fx_actor: Actor
    ) -> None:
        """Verify the sources of another account's project cannot be listed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        with pytest.raises(NotFoundError):
            await fx_service().list(fx_actor, fx_strangers_book.project.id, SliceRequest())


class TestGet:
    """Tests for SourceService.get()."""

    async def test_returns_the_source_of_the_project(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify a source is read by its identifier through its project.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        assert await fx_service().get(fx_actor, fx_book.project.id, fx_book.second.id) == fx_book.second

    async def test_source_of_another_project_of_the_actor_is_not_found(
        self, fx_service: Callable[[], SourceService], fx_database: InMemoryDatabase, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify a source is found only through the project holding it, even for an owner of both projects.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        other = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, other)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, other.id, fx_book.first.id)

    async def test_missing_source_and_a_strangers_source_are_not_found(
        self, fx_service: Callable[[], SourceService], fx_book: Book, fx_strangers_book: Book, fx_actor: Actor
    ) -> None:
        """Verify an identifier no source has and a source of another account's project answer alike.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        attempts = [
            (fx_book.project.id, SourceId(uuid4())),
            (fx_strangers_book.project.id, fx_strangers_book.first.id),
        ]

        for project_id, source_id in attempts:
            with pytest.raises(NotFoundError):
                await fx_service().get(fx_actor, project_id, source_id)


class TestScans:
    """Tests for SourceService.scans()."""

    async def test_lists_the_scans_of_the_project_source_by_source(
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
        scans = await fx_service().scans(fx_actor, fx_book.project.id, SliceRequest())

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
            fx_actor, fx_book.project.id, SliceRequest(offset=1, limit=1), source_id=fx_book.second.id
        )

        expect(window.items == [fx_book.scans[SCANS_PER_SOURCE + 1]])
        expect(window.total == SCANS_PER_SOURCE)
        assert_expectations()

    async def test_source_of_another_project_is_not_found(
        self, fx_service: Callable[[], SourceService], fx_database: InMemoryDatabase, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify the scans of a source are listed only through the project holding it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        other = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, other)

        with pytest.raises(NotFoundError):
            await fx_service().scans(fx_actor, other.id, SliceRequest(), source_id=fx_book.first.id)

    async def test_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], SourceService], fx_strangers_book: Book, fx_actor: Actor
    ) -> None:
        """Verify the scans of another account's project cannot be listed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        with pytest.raises(NotFoundError):
            await fx_service().scans(fx_actor, fx_strangers_book.project.id, SliceRequest())


class TestDelete:
    """Tests for SourceService.delete()."""

    async def test_removes_the_source_its_scans_and_their_files_but_keeps_the_page(
        self,
        fx_service: Callable[[], SourceService],
        fx_database: InMemoryDatabase,
        fx_book: Book,
        fx_storage_root: Path,
        fx_actor: Actor,
    ) -> None:
        """Verify the source, its scans and their files go, the other source stays, and the page keeps its image.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        await fx_service().delete(fx_actor, fx_book.project.id, fx_book.first.id)

        uow = InMemoryUnitOfWork(fx_database)
        remaining = await fx_service().list(fx_actor, fx_book.project.id, SliceRequest())
        scans = await fx_service().scans(fx_actor, fx_book.project.id, SliceRequest())
        image = ProjectKeys(fx_book.project.id).version_rendition(fx_book.version, Rendition.FULL_JPEG)
        expect(remaining.items == [fx_book.second])
        expect(scans.items == fx_book.scans[SCANS_PER_SOURCE:])
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (False, False))
        expect(on_disk(fx_storage_root, fx_book, fx_book.second) == (True, True))
        expect(await uow.pages.get(fx_book.page.id) == evolve(fx_book.page, scan_id=None))
        expect(await uow.page_versions.list_for_page(fx_book.page.id) == [fx_book.version])
        expect((fx_storage_root / image).is_file())
        assert_expectations()

    async def test_import_in_progress_is_a_conflict_and_removes_nothing(
        self,
        fx_service: Callable[[], SourceService],
        fx_database: InMemoryDatabase,
        fx_book: Book,
        fx_storage_root: Path,
        fx_actor: Actor,
    ) -> None:
        """Verify a source cannot be deleted while the project imports, whose job may be writing its scans.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        uow = InMemoryUnitOfWork(fx_database)
        async with uow.change():
            await uow.jobs.add(make_job(project_id=fx_book.project.id, state=JobState.RUNNING))

        with pytest.raises(ConflictError):
            await fx_service().delete(fx_actor, fx_book.project.id, fx_book.first.id)

        expect(await fx_service().get(fx_actor, fx_book.project.id, fx_book.first.id) == fx_book.first)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (True, True))
        assert_expectations()

    async def test_finished_import_does_not_block_the_deletion(
        self, fx_service: Callable[[], SourceService], fx_database: InMemoryDatabase, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify a failed import leaves the project free to delete the source it failed on, by hand.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the acting account.
        :type fx_book: Book
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        uow = InMemoryUnitOfWork(fx_database)
        async with uow.change():
            await uow.jobs.add(make_job(project_id=fx_book.project.id, state=JobState.FAILED))

        await fx_service().delete(fx_actor, fx_book.project.id, fx_book.first.id)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, fx_book.project.id, fx_book.first.id)

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
            await fx_flaky_service().delete(fx_actor, fx_book.project.id, fx_book.first.id)
        kept = await fx_flaky_service().get(fx_actor, fx_book.project.id, fx_book.first.id)
        await fx_flaky_service().delete(fx_actor, fx_book.project.id, fx_book.first.id)

        expect(kept == fx_book.first)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (False, False))
        with pytest.raises(NotFoundError):
            await fx_flaky_service().get(fx_actor, fx_book.project.id, fx_book.first.id)
        assert_expectations()

    async def test_source_of_another_account_is_not_found_and_kept(
        self, fx_service: Callable[[], SourceService], fx_strangers_book: Book, fx_storage_root: Path, fx_actor: Actor
    ) -> None:
        """Verify the actor cannot delete another account's source, whose files stay.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], SourceService]
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, fx_strangers_book.project.id, fx_strangers_book.first.id)

        assert on_disk(fx_storage_root, fx_strangers_book, fx_strangers_book.first) == (True, True)
