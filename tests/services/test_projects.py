"""Tests for the project use cases, against the database of the test and the local stores over a temporary directory."""

import io
import zipfile
from datetime import timedelta
from typing import TYPE_CHECKING, override

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalBookArchive, LocalSourceStore
from bookreviver.domain.changes import BookDetailsChanges, ProjectChanges
from bookreviver.domain.enums import ImagePolicy, Orthography
from bookreviver.domain.errors import DomainError, NotFoundError
from bookreviver.domain.values import BookDetails, SliceRequest
from bookreviver.services.library import Library
from bookreviver.services.projects import ProjectFiles, ProjectService
from bookreviver.services.stage_summaries import StageSummaries
from tests.helpers.builders import EPOCH, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.clock import FixedClock
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import commit_project, store_project
from tests.helpers.storage import BookFiles

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

NOW = EPOCH + timedelta(days=1)
PAGE_COUNT: int = 2
NEW_TITLE: str = 'Renamed'
# The name of the database in the archive of a book
DATABASE_NAME: str = 'book.db'


def _service(uow: UnitOfWork, sources: LocalSourceStore, assets: LocalAssetStore, root: Path) -> ProjectService:
    """Build the project service for one request, over a unit of work, a catalogue with no processors and no jobs.

    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :param sources: Source store of the test.
    :type sources: LocalSourceStore
    :param assets: Asset store of the test.
    :type assets: LocalAssetStore
    :param root: Storage root of the test, which the archive of the books packs and unpacks under.
    :type root: Path
    :returns: The service, with a clock standing at ``NOW``.
    :rtype: ProjectService
    """
    clock = FixedClock(NOW)
    board = FakeJobBoard(runner=ScriptedRunner(), publisher=RecordingEventBus(), clock=clock)
    stages = StageSummaries(catalogue=FakeCatalogue([]), board=board)
    return ProjectService(
        uow=uow,
        library=Library(uow=uow),
        clock=clock,
        files=ProjectFiles(sources=sources, assets=assets, archive=LocalBookArchive(root=root)),
        stages=stages,
    )


@pytest.fixture
def fx_service(
    fx_uow: UnitOfWorkFactory,
    fx_source_store: LocalSourceStore,
    fx_asset_store: LocalAssetStore,
    fx_storage_root: Path,
) -> Callable[[], ProjectService]:
    """Return a function building the service for one request, with a clock standing at ``NOW``.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_source_store: Local source store over the test's storage root.
    :type fx_source_store: LocalSourceStore
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_storage_root: Storage root of the test.
    :type fx_storage_root: Path
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], ProjectService]
    """
    return lambda: _service(fx_uow(), fx_source_store, fx_asset_store, fx_storage_root)


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
    fx_uow: UnitOfWorkFactory, fx_asset_store: LocalAssetStore, fx_storage_root: Path
) -> Callable[[], ProjectService]:
    """Return a function building the service for one request over a source store whose first deletion fails.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :returns: Function building a service over a new unit of work and the one failing source store.
    :rtype: Callable[[], ProjectService]
    """
    sources = SourceStoreFailingOnce(root=fx_storage_root)
    return lambda: _service(fx_uow(), sources, fx_asset_store, fx_storage_root)


async def _stored(uow: UnitOfWork, project: Project) -> Project:
    """Read the committed state of a project.

    :param uow: Unit of work to read through.
    :type uow: UnitOfWork
    :param project: Project to read back.
    :type project: Project
    :returns: The project as committed.
    :rtype: Project
    """
    return await uow.read_book(project.id, lambda repos: repos.projects.get(project.id))


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
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify another account's project is hidden and the actor's come most recently updated first.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        older, newer, other = make_project(minutes=1), make_project(minutes=2), make_project(minutes=3)
        for project, owner in ((older, fx_actor.account_id), (newer, fx_actor.account_id), (other, new_account_id())):
            await commit_project(fx_database, fx_uow(), project, owner=owner)

        result = await fx_service().list(fx_actor, SliceRequest())

        expect([item.project.id for item in result.items] == [newer.id, older.id])
        expect(result.total == 2)
        assert_expectations()


class TestCreate:
    """Tests for ProjectService.create()."""

    async def test_creates_an_empty_project_owned_by_the_actor(
        self, fx_service: Callable[[], ProjectService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the project is committed with the actor as owner and the clock's time on both timestamps.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        details = BookDetails(title='Book', orthography=Orthography.PRE_REFORM)

        overview = await fx_service().create(fx_actor, details)

        stored = await _stored(fx_uow(), overview.project)
        entry = await fx_uow().read(lambda repos: repos.library.get(stored.id))
        expect(stored == overview.project)
        expect((entry.owner_id, entry.added_at) == (fx_actor.account_id, NOW))
        expect(stored.details == details)
        expect((stored.created_at, stored.updated_at) == (NOW, NOW))
        expect(overview.page_count == 0)
        assert_expectations()


class TestGet:
    """Tests for ProjectService.get()."""

    async def test_returns_the_project_with_the_counts_of_its_book(
        self, fx_service: Callable[[], ProjectService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the overview counts the included pages, the sources and the scans of the project.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        source = make_source()
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        pages = [make_page(order_key=f'a{scan.number}', scan=scan) for scan in scans]
        # A duplicate scan kept out of the book is no page of it
        await store_project(
            fx_uow(),
            project,
            *pages[:-1],
            evolve(pages[-1], included=False),
            owner=fx_actor.account_id,
            sources=[source],
            scans=scans,
        )

        overview = await fx_service().get(fx_actor, project.id)

        assert (overview.project, overview.page_count, overview.source_count, overview.scan_count) == (
            project,
            PAGE_COUNT - 1,
            1,
            PAGE_COUNT,
        )

    async def test_another_accounts_project_is_not_found(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify a project of another account is reported as missing, not as forbidden.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=new_account_id())

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, project.id)


class TestUpdate:
    """Tests for ProjectService.update()."""

    async def test_sets_the_image_policy(
        self, fx_service: Callable[[], ProjectService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the image policy of the project changes.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        changed = await fx_service().update(fx_actor, project.id, ProjectChanges(image_policy=ImagePolicy.LOSSLESS))

        assert changed.project.image_policy == ImagePolicy.LOSSLESS

    async def test_changes_given_fields_and_touches_the_project(
        self,
        fx_service: Callable[[], ProjectService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify the given fields change, the others stay, and only the update time moves.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(title='Old')
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        overview = await fx_service().update(
            fx_actor, project.id, ProjectChanges(details=BookDetailsChanges(title=NEW_TITLE))
        )

        stored = await _stored(fx_uow(), project)
        expect(stored == overview.project)
        expect(stored.details == BookDetails(title=NEW_TITLE))
        expect((stored.created_at, stored.updated_at) == (project.created_at, NOW))
        assert_expectations()

    async def test_another_accounts_project_is_not_found_and_unchanged(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify the actor cannot change another account's project.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=new_account_id())

        with pytest.raises(NotFoundError):
            await fx_service().update(fx_actor, project.id, ProjectChanges(details=BookDetailsChanges(title=NEW_TITLE)))

        assert await _stored(fx_uow(), project) == project


class TestDelete:
    """Tests for ProjectService.delete()."""

    async def test_removes_the_project_then_all_its_files(
        self,
        fx_service: Callable[[], ProjectService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify the row, its pages, its source and its derived files go, and a neighbour's files stay.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        doomed, kept = make_project(), make_project()
        doomed_page, kept_page = make_page(), make_page()
        for project, page in ((doomed, doomed_page), (kept, kept_page)):
            await store_project(fx_uow(), project, page, owner=fx_actor.account_id)
            await fx_files.store(project.id, page)

        await fx_service().delete(fx_actor, doomed.id)

        remaining = await fx_uow().read(lambda repos: repos.library.list_for_owner(fx_actor.account_id, SliceRequest()))
        expect([entry.project_id for entry in remaining.items] == [kept.id])
        expect(doomed.id not in await fx_uow().stored_books())
        expect(fx_files.gone(doomed.id))
        expect(fx_files.kept(kept_page))
        assert_expectations()

    async def test_failed_deletion_can_be_repeated_until_nothing_is_left(
        self,
        fx_flaky_service: Callable[[], ProjectService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify a deletion stopped by a storage failure keeps the project, and repeating it removes everything.

        :param fx_flaky_service: Function building the service over a source store whose first deletion fails.
        :type fx_flaky_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        page = make_page()
        await store_project(fx_uow(), project, page, owner=fx_actor.account_id)
        await fx_files.store(project.id, page)

        with pytest.raises(StoreFailedError):
            await fx_flaky_service().delete(fx_actor, project.id)

        expect(await _stored(fx_uow(), project) == project)
        assert_expectations()
        await fx_flaky_service().delete(fx_actor, project.id)

        with pytest.raises(NotFoundError):
            await _stored(fx_uow(), project)
        assert fx_files.gone(project.id)

    async def test_another_accounts_project_is_not_found_and_kept(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify the actor cannot delete another account's project or its files.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books, among them the other account's.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        page = make_page()
        await commit_project(fx_database, fx_uow(), project, page, owner=new_account_id())
        await fx_files.store(project.id, page)

        with pytest.raises(NotFoundError):
            await fx_service().delete(fx_actor, project.id)

        expect(await _stored(fx_uow(), project) == project)
        expect(fx_files.kept(page))
        assert_expectations()


class TestDeleteAll:
    """Tests for ProjectService.delete_all()."""

    async def test_removes_every_project_of_the_actor_with_its_files(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify every project of the actor goes with its files, and another account's project stays.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        owned = [make_project(minutes=minute) for minute in range(PAGE_COUNT + 1)]
        kept = make_project()
        pages = {project.id: make_page() for project in (*owned, kept)}
        for project in (*owned, kept):
            owner = new_account_id() if project is kept else fx_actor.account_id
            await commit_project(fx_database, fx_uow(), project, pages[project.id], owner=owner)
            await fx_files.store(project.id, pages[project.id])

        await fx_service().delete_all(fx_actor)

        remaining = await fx_uow().read(lambda repos: repos.library.list_for_owner(fx_actor.account_id, SliceRequest()))
        expect(remaining.total == 0)
        expect(all(fx_files.gone(project.id) for project in owned))
        expect(await _stored(fx_uow(), kept) == kept)
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


async def _archive(service: ProjectService, actor: Actor, project_id: ProjectId) -> io.BytesIO:
    """Read the whole archive of a book the actor owns, as the browser downloads it.

    :param service: The project service of the request.
    :type service: ProjectService
    :param actor: Account the service acts for.
    :type actor: Actor
    :param project_id: The book.
    :type project_id: ProjectId
    :returns: The archive, ready to be read from its start.
    :rtype: io.BytesIO
    """
    archived = await service.export(actor, project_id)
    return io.BytesIO(b''.join([piece async for piece in archived.pieces]))


class TestExportAndImport:
    """Tests for ProjectService.export() and import_archive(), which carry a book from one instance to another."""

    async def test_an_imported_book_is_a_new_book_of_the_actor_with_the_pages_and_files_of_the_archive(
        self,
        fx_service: Callable[[], ProjectService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify the import keeps the title, the page and its file, under a new identifier the actor owns.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service works on.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, page = make_project(), make_page()
        await store_project(fx_uow(), project, page, owner=fx_actor.account_id)
        await fx_files.store(project.id, page)
        archive = await _archive(fx_service(), fx_actor, project.id)

        imported = await fx_service().import_archive(fx_actor, archive)

        copy_id = imported.project.id
        pages = await fx_uow().read_book(copy_id, lambda repos: repos.pages.list_window(SliceRequest()).items)
        listed = await fx_service().list(fx_actor, SliceRequest())
        expect(copy_id != project.id)
        expect(imported.project.details == project.details)
        expect([stored.id for stored in pages] == [page.id])
        expect(fx_files.kept(page, project_id=copy_id))
        expect([overview.project.id for overview in listed.items] == [copy_id, project.id])
        assert_expectations()

    async def test_a_file_that_is_no_archive_of_a_book_leaves_no_book_behind(
        self, fx_service: Callable[[], ProjectService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify an archive whose database holds no book is refused and its unpacked files are removed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        written = io.BytesIO()
        with zipfile.ZipFile(written, mode='w') as archive:
            archive.writestr(DATABASE_NAME, b'not a database')
        written.seek(0)

        with pytest.raises(DomainError):
            await fx_service().import_archive(fx_actor, written)

        expect(await fx_uow().stored_books() == [])
        expect((await fx_service().list(fx_actor, SliceRequest())).total == 0)
        assert_expectations()

    async def test_another_accounts_book_is_not_exported(
        self,
        fx_service: Callable[[], ProjectService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
    ) -> None:
        """Verify the export of a book of another account is refused as a missing book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_database: The catalogue of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=new_account_id())

        with pytest.raises(NotFoundError):
            await fx_service().export(fx_actor, project.id)


class TestSweep:
    """Tests for ProjectService.sweep(), which finishes the creations and deletions that were broken off half way."""

    async def test_a_book_stored_without_an_entry_goes_with_its_files(
        self,
        fx_service: Callable[[], ProjectService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify a creation that stored the book and never listed it is undone, and a listed book stays.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        broken, whole = make_project(), make_project()
        broken_page, whole_page = make_page(), make_page()
        await fx_uow().create_book(broken)
        await store_project(fx_uow(), whole, whole_page, owner=fx_actor.account_id)
        for project, page in ((broken, broken_page), (whole, whole_page)):
            await fx_files.store(project.id, page)

        await fx_service().sweep()

        expect(set(await fx_uow().stored_books()) == {whole.id})
        expect(fx_files.gone(broken.id))
        expect(fx_files.kept(whole_page))
        assert_expectations()

    async def test_an_entry_without_a_book_goes_with_the_files_that_are_left(
        self,
        fx_service: Callable[[], ProjectService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
    ) -> None:
        """Verify a deletion that removed the book and never removed its entry is finished.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], ProjectService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        page = make_page()
        await store_project(fx_uow(), project, page, owner=fx_actor.account_id)
        await fx_files.store(project.id, page)
        await fx_uow().delete_book(project.id)

        await fx_service().sweep()

        entries = await fx_uow().read(lambda repos: repos.library.list_all())
        expect(entries == [])
        expect(fx_files.gone(project.id))
        assert_expectations()


class SourceStoreWaiting(LocalSourceStore):
    """The local source store, whose project deletion stops until the test lets it go.

    :ivar reached: Set when the deletion of the files of a project has begun.
    :ivar proceed: Set by the test to let the deletion carry on.
    """

    def __init__(self, *, root: Path) -> None:
        """Keep the files under ``root``, with both events unset.

        :param root: Storage root, created on the first write.
        :type root: Path
        """
        super().__init__(root=root)
        self.reached = anyio.Event()
        self.proceed = anyio.Event()

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        """Tell the test the deletion has begun, wait for it, and remove the files.

        :param project_id: Project whose source files are removed.
        :type project_id: ProjectId
        """
        self.reached.set()
        await self.proceed.wait()
        await super().delete_project(project_id)


class TestDeleteHoldsNoTransactionOverTheFiles:
    """Tests for the deletion of a project, whose files go outside any transaction and whose row goes in one."""

    async def test_a_change_of_the_book_opens_while_its_files_are_being_removed(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: LocalAssetStore,
        fx_storage_root: Path,
        fx_actor: Actor,
    ) -> None:
        """Verify a change of the book is not kept waiting by a slow store, and the deletion ends the row after it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        sources = SourceStoreWaiting(root=fx_storage_root)
        opened = False

        def mark_opened(_repos: BookRepositories, _project: Project) -> None:
            """Note that the change of the book was granted.

            :param _repos: Repositories of the transaction, which the work does not use.
            :type _repos: BookRepositories
            :param _project: The project of the book, which the work does not use.
            :type _project: Project
            """
            nonlocal opened
            opened = True

        async def open_a_change() -> None:
            """Open and leave a change of the book of the project, which has to be granted at once."""
            await fx_uow().change_book(project.id, mark_opened)

        async with anyio.create_task_group() as group:
            group.start_soon(_service(fx_uow(), sources, fx_asset_store, fx_storage_root).delete, fx_actor, project.id)
            await sources.reached.wait()
            group.start_soon(open_a_change)
            await anyio.wait_all_tasks_blocked()
            expect(opened)
            sources.proceed.set()

        with pytest.raises(NotFoundError):
            await _stored(fx_uow(), project)
        assert_expectations()
