"""Tests for the project use cases, against in-memory persistence and the local stores over a temporary directory."""

from datetime import timedelta
from typing import TYPE_CHECKING, override

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.changes import BookDetailsChanges
from bookreviver.domain.enums import Orthography
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.values import BookDetails, SliceRequest
from bookreviver.services.projects import ProjectService
from tests.helpers.builders import EPOCH, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project
from tests.helpers.storage import BookFiles

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Project
    from bookreviver.domain.ids import ProjectId

pytestmark = pytest.mark.anyio

NOW = EPOCH + timedelta(days=1)
PAGE_COUNT: int = 2
NEW_TITLE: str = 'Renamed'


@pytest.fixture
def fx_service(
    fx_database: InMemoryDatabase, fx_source_store: LocalSourceStore, fx_asset_store: LocalAssetStore
) -> Callable[[], ProjectService]:
    """Return a function building the service for one request, with a clock standing at ``NOW``.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], ProjectService]
    """
    return lambda: ProjectService(
        uow=InMemoryUnitOfWork(fx_database), clock=FixedClock(NOW), sources=fx_source_store, assets=fx_asset_store
    )


class StoreFailedError(OSError):
    """Raised by the source store of a test in place of a real storage failure."""


class SourceStoreFailingOnce(LocalSourceStore):
    """The local source store, whose first project deletion fails before it removes anything."""

    def __init__(self, *, root: Path) -> None:
        """Keep the sources under ``root`` and fail the first deletion.

        :param root: Storage root shared with the asset store.
        :type root: Path
        """
        super().__init__(root=root)
        self._failed = False

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        """Fail on the first call, as a storage outage would, and remove the project's source on the later ones.

        :param project_id: Project whose source files are removed.
        :type project_id: ProjectId
        :raises StoreFailedError: On the first call, with nothing removed.
        """
        if not self._failed:
            self._failed = True
            raise StoreFailedError
        await super().delete_project(project_id)


@pytest.fixture
def fx_flaky_service(
    fx_database: InMemoryDatabase, fx_asset_store: LocalAssetStore, fx_storage_root: Path
) -> Callable[[], ProjectService]:
    """Return a function building the service for one request over a source store whose first deletion fails.

    :param fx_database: In-memory database every request of the test shares.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :returns: Function building a service over a new unit of work and the one failing source store.
    :rtype: Callable[[], ProjectService]
    """
    sources = SourceStoreFailingOnce(root=fx_storage_root)
    return lambda: ProjectService(
        uow=InMemoryUnitOfWork(fx_database), clock=FixedClock(NOW), sources=sources, assets=fx_asset_store
    )


async def _stored(database: InMemoryDatabase, project: Project) -> Project:
    """Read the committed state of a project.

    :param database: In-memory database to read from.
    :type database: InMemoryDatabase
    :param project: Project to read back.
    :type project: Project
    :returns: The project as committed.
    :rtype: Project
    """
    return await InMemoryUnitOfWork(database).projects.get(project.id)


@pytest.fixture
def fx_files(fx_source_store: LocalSourceStore, fx_asset_store: LocalAssetStore, fx_storage_root: Path) -> BookFiles:
    """Return the files of imported books in the stores the service works on.

    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :returns: Files of the test's books.
    :rtype: BookFiles
    """
    return BookFiles(sources=fx_source_store, assets=fx_asset_store, root=fx_storage_root)


class TestList:
    """Tests for ProjectService.list()."""

    async def test_lists_only_the_actors_projects_newest_first(
        self, fx_service: Callable[[], ProjectService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify another account's project is hidden and the actor's come most recently updated first.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        older = make_project(owner_id=fx_actor.account_id, minutes=1)
        newer = make_project(owner_id=fx_actor.account_id, minutes=2)
        for project in (older, newer, make_project(owner_id=new_account_id(), minutes=3)):
            await commit_project(fx_database, project)

        result = await fx_service().list(fx_actor, SliceRequest())

        expect([item.project.id for item in result.items] == [newer.id, older.id])
        expect(result.total == 2)
        assert_expectations()


class TestCreate:
    """Tests for ProjectService.create()."""

    async def test_creates_an_empty_project_owned_by_the_actor(
        self, fx_service: Callable[[], ProjectService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the project is committed with the actor as owner and the clock's time on both timestamps.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        details = BookDetails(title='Book', orthography=Orthography.PRE_REFORM)

        overview = await fx_service().create(fx_actor, details)

        stored = await _stored(fx_database, overview.project)
        expect(stored == overview.project)
        expect(stored.owner_id == fx_actor.account_id)
        expect(stored.details == details)
        expect((stored.created_at, stored.updated_at) == (NOW, NOW))
        expect(overview.page_count == 0)
        assert_expectations()


class TestGet:
    """Tests for ProjectService.get()."""

    async def test_returns_the_project_with_the_counts_of_its_book(
        self, fx_service: Callable[[], ProjectService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the overview counts the included pages, the sources and the scans of the project.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        pages = [make_page(project_id=project.id, order_key=f'a{scan.number}', scan=scan) for scan in scans]
        # A duplicate scan kept out of the book is no page of it
        await commit_project(
            fx_database, project, *pages[:-1], evolve(pages[-1], included=False), sources=[source], scans=scans
        )

        overview = await fx_service().get(fx_actor, project.id)

        assert (overview.project, overview.page_count, overview.source_count, overview.scan_count) == (
            project,
            PAGE_COUNT - 1,
            1,
            PAGE_COUNT,
        )

    async def test_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], ProjectService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a project of another account is reported as missing, not as forbidden.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, project.id)


class TestUpdateDetails:
    """Tests for ProjectService.update_details()."""

    async def test_changes_given_fields_and_touches_the_project(
        self, fx_service: Callable[[], ProjectService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the given fields change, the others stay, and only the update time moves.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id, title='Old')
        await commit_project(fx_database, project)

        overview = await fx_service().update_details(fx_actor, project.id, BookDetailsChanges(title=NEW_TITLE))

        stored = await _stored(fx_database, project)
        expect(stored == overview.project)
        expect(stored.details == BookDetails(title=NEW_TITLE))
        expect((stored.created_at, stored.updated_at) == (project.created_at, NOW))
        assert_expectations()

    async def test_another_accounts_project_is_not_found_and_unchanged(
        self, fx_service: Callable[[], ProjectService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the actor cannot change another account's project.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)

        with pytest.raises(NotFoundError):
            await fx_service().update_details(fx_actor, project.id, BookDetailsChanges(title=NEW_TITLE))

        assert await _stored(fx_database, project) == project


class TestDelete:
    """Tests for ProjectService.delete()."""

    async def test_removes_the_project_then_all_its_files(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: InMemoryDatabase,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify the row, its pages, its source and its derived files go, and a neighbour's files stay.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        doomed, kept = make_project(owner_id=fx_actor.account_id), make_project(owner_id=fx_actor.account_id)
        doomed_page, kept_page = make_page(project_id=doomed.id), make_page(project_id=kept.id)
        for project, page in ((doomed, doomed_page), (kept, kept_page)):
            await commit_project(fx_database, project, page)
            await fx_files.store(page)

        await fx_service().delete(fx_actor, doomed.id)

        uow = InMemoryUnitOfWork(fx_database)
        remaining = await uow.projects.list_for_owner(fx_actor.account_id, SliceRequest())
        expect([item.project.id for item in remaining.items] == [kept.id])
        expect((await uow.pages.list_for_project(doomed.id, SliceRequest())).total == 0)
        expect(fx_files.gone(doomed.id))
        expect(fx_files.kept(kept_page))
        assert_expectations()

    async def test_failed_deletion_can_be_repeated_until_nothing_is_left(
        self,
        fx_flaky_service: Callable[[], ProjectService],
        fx_database: InMemoryDatabase,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify a deletion stopped by a storage failure keeps the project, and repeating it removes everything.

        :param fx_flaky_service: Function building the service over a source store whose first deletion fails.
        :type fx_flaky_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)
        await fx_files.store(page)

        with pytest.raises(StoreFailedError):
            await fx_flaky_service().delete(fx_actor, project.id)

        expect(await _stored(fx_database, project) == project)
        assert_expectations()
        await fx_flaky_service().delete(fx_actor, project.id)

        with pytest.raises(NotFoundError):
            await _stored(fx_database, project)
        assert fx_files.gone(project.id)

    async def test_another_accounts_project_is_not_found_and_kept(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: InMemoryDatabase,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify the actor cannot delete another account's project or its files.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_files: Files of imported books, among them the other account's.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=new_account_id())
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)
        await fx_files.store(page)

        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, project.id)

        expect(await _stored(fx_database, project) == project)
        expect(fx_files.kept(page))
        assert_expectations()


class TestDeleteAll:
    """Tests for ProjectService.delete_all()."""

    async def test_removes_every_project_of_the_actor_with_its_files(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: InMemoryDatabase,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify every project of the actor goes with its files, and another account's project stays.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        owned = [make_project(owner_id=fx_actor.account_id, minutes=minute) for minute in range(PAGE_COUNT + 1)]
        kept = make_project(owner_id=new_account_id())
        pages = {project.id: make_page(project_id=project.id) for project in (*owned, kept)}
        for project in (*owned, kept):
            await commit_project(fx_database, project, pages[project.id])
            await fx_files.store(pages[project.id])

        await fx_service().delete_all(fx_actor)

        remaining = await InMemoryUnitOfWork(fx_database).projects.list_for_owner(fx_actor.account_id, SliceRequest())
        expect(remaining.total == 0)
        expect(all(fx_files.gone(project.id) for project in owned))
        expect(await _stored(fx_database, kept) == kept)
        expect(fx_files.kept(pages[kept.id]))
        assert_expectations()

    async def test_actor_without_projects_is_not_an_error(
        self, fx_service: Callable[[], ProjectService], fx_actor: Actor
    ) -> None:
        """Verify deleting the projects of an account that has none succeeds, as for a fresh account.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        await fx_service().delete_all(fx_actor)
