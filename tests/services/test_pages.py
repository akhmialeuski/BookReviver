"""Tests for the page use cases, against in-memory persistence."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import Stage, VersionState
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageId, StorageKey
from bookreviver.domain.keys import KeySegment, ProjectKeys
from bookreviver.domain.values import Renditions, SliceRequest
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project
    from bookreviver.services.pages import PageService

pytestmark = pytest.mark.anyio

# Stored against the book order, and sorting as a0, a0V, a1 in bytes
STORED_KEYS: list[str] = ['a1', 'a0', 'a0V']
BOOK_ORDER: list[int] = [1, 2, 0]
WINDOW: SliceRequest = SliceRequest(offset=1, limit=1)
FILE_CONTENT: bytes = b'derived file'


def _ready(version: PageVersion) -> PageVersion:
    """Make a version that has its files written.

    :param version: A pending version.
    :type version: PageVersion
    :returns: The version as ready, with its renditions ready.
    :rtype: PageVersion
    """
    return evolve(version, renditions=Renditions(ready=True), state=VersionState.READY)


async def _commit_book(database: InMemoryDatabase, project: Project) -> tuple[list[Page], list[PageVersion]]:
    """Commit a book of three pages stored against the book order, the middle one in the book a placeholder.

    :param database: In-memory database to commit into.
    :type database: InMemoryDatabase
    :param project: Project of the book.
    :type project: Project
    :returns: The pages in the order they were stored, and the base versions of the two that have one.
    :rtype: tuple[list[Page], list[PageVersion]]
    """
    source = make_source(project_id=project.id)
    scans = [make_scan(source=source, number=number) for number in range(2)]
    shown = [scans[0], None, scans[1]]
    pages = [
        make_page(project_id=project.id, order_key=key, scan=scan) for key, scan in zip(STORED_KEYS, shown, strict=True)
    ]
    versions = [make_page_version(page_id=pages[index].id) for index in (0, 2)]
    await commit_project(database, project, *pages, sources=[source], scans=scans, versions=versions)
    return pages, versions


class TestManifest:
    """Tests for PageService.manifest()."""

    async def test_lists_the_pages_in_book_order_with_their_positions_and_versions(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify pages come in the byte order of their keys, numbered from zero, each with its own base version.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages, versions = await _commit_book(fx_database, project)

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        expect([overview.page for overview in manifest.items] == [pages[index] for index in BOOK_ORDER])
        expect([overview.position for overview in manifest.items] == [0, 1, 2])
        expect([overview.image_version for overview in manifest.items] == [None, versions[1], versions[0]])
        expect(manifest.total == len(pages))
        assert_expectations()

    async def test_a_window_continues_the_positions_of_the_book(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a window starting at an offset numbers its pages from that offset and reports the whole book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages, _ = await _commit_book(fx_database, project)

        manifest = await fx_service().manifest(fx_actor, project.id, WINDOW)

        expect([(overview.page, overview.position) for overview in manifest.items] == [(pages[2], 1)])
        expect(manifest.total == len(pages))
        assert_expectations()

    async def test_shows_the_newest_base_version_of_a_page(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page cut from its scan again shows the base version created last.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        old, new = make_page_version(page_id=page.id, minutes=1), make_page_version(page_id=page.id, minutes=2)
        await commit_project(fx_database, project, page, versions=[new, old])

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        assert [overview.image_version for overview in manifest.items] == [new]

    async def test_shows_the_current_version_of_the_latest_stage_that_has_an_image(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the page shows its deskewed version, and not the base version it was made from or the regions without an image.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        base = _ready(make_page_version(page_id=page.id, minutes=1))
        deskewed = evolve(_ready(make_page_version(page_id=page.id, minutes=2)), stage=Stage.GEOMETRY, input_id=base.id)
        regions = evolve(make_page_version(page_id=page.id, minutes=3), stage=Stage.LAYOUT, input_id=deskewed.id)
        await commit_project(fx_database, project, page, versions=[base, deskewed, regions])
        uow = InMemoryUnitOfWork(fx_database)
        for version in (base, deskewed):
            await uow.page_stages.save(
                make_page_stage(page_id=page.id, stage=version.stage, head_version_id=version.id)
            )
        await uow.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.LAYOUT, head_version_id=regions.id))
        await uow.commit()

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        assert [overview.image_version for overview in manifest.items] == [deskewed]

    async def test_shows_the_current_version_even_when_a_newer_base_version_exists(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a version the user chose as current is shown over a base version that was made after it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        chosen, newer = (_ready(make_page_version(page_id=page.id, minutes=minutes)) for minutes in (1, 2))
        await commit_project(fx_database, project, page, versions=[chosen, newer])
        uow = InMemoryUnitOfWork(fx_database)
        await uow.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.PAGE_SPLIT, head_version_id=chosen.id))
        await uow.commit()

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        assert [overview.image_version for overview in manifest.items] == [chosen]

    async def test_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the pages of another account's project cannot be listed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=new_account_id())
        await _commit_book(fx_database, project)

        with pytest.raises(NotFoundError):
            await fx_service().manifest(fx_actor, project.id, SliceRequest())

    async def test_included_only_leaves_out_the_pages_kept_out_and_numbers_the_rest(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a listing of the included pages skips an excluded page, and numbers the listed pages among themselves.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages = [make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1', 'a2')]
        pages[1] = evolve(pages[1], included=False)
        await commit_project(fx_database, project, *pages)

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest(), included_only=True)

        expect([overview.page for overview in manifest.items] == [pages[0], pages[2]])
        expect([overview.position for overview in manifest.items] == [0, 1])
        expect(manifest.total == len(manifest.items))
        assert_expectations()

    async def test_names_the_source_of_a_page_with_a_scan_and_none_for_a_placeholder(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify each page of the window knows the source of its scan, which selects the pages of one source.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await _commit_book(fx_database, project)
        source = (await InMemoryUnitOfWork(fx_database).sources.list_for_project(project.id))[0]

        manifest = await fx_service().manifest(fx_actor, project.id, SliceRequest())

        assert [overview.source_id for overview in manifest.items] == [None, source.id, source.id]


class TestGet:
    """Tests for PageService.get()."""

    async def test_returns_the_page_with_its_position_and_base_version(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page read by its identifier knows its place in the book and its base version.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        pages, versions = await _commit_book(fx_database, project)

        overview = await fx_service().get(fx_actor, project.id, pages[0].id)

        expect(overview.page == pages[0])
        expect(overview.position == len(pages) - 1)
        expect(overview.image_version == versions[0])
        assert_expectations()

    async def test_page_of_another_project_of_the_actor_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page is found only through the project that holds it, even for an owner of both projects.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project, holder = (make_project(owner_id=fx_actor.account_id) for _ in range(2))
        page = make_page(project_id=holder.id)
        await commit_project(fx_database, project)
        await commit_project(fx_database, holder, page)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, project.id, page.id)

    async def test_page_of_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the actor cannot read a page through the project of another account.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        holder = make_project(owner_id=new_account_id())
        page = make_page(project_id=holder.id)
        await commit_project(fx_database, holder, page)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, holder.id, page.id)

    async def test_missing_page_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify an identifier no page has is reported as not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, project.id, PageId(uuid4()))


class TestOpenAsset:
    """Tests for PageService.open_asset()."""

    async def test_gives_the_path_of_a_file_of_the_actors_project(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the owner reads a stored derived file through the path the service hands out.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        key = ProjectKeys(project.id).book
        async with fx_asset_store.writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        async with fx_service().open_asset(fx_actor, key) as path:
            assert path.read_bytes() == FILE_CONTENT

    async def test_another_accounts_file_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_database: InMemoryDatabase,
        fx_asset_store: LocalAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a stored file of another account's project is reported like a missing one.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)
        key = ProjectKeys(project.id).book
        async with fx_asset_store.writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(fx_actor, key):
                pytest.fail('The file of another account was opened.')

    @pytest.mark.parametrize(
        'area', [KeySegment.SOURCES, KeySegment.INCOMING, 'notes'], ids=['sources', 'incoming', 'unknown-area']
    )
    async def test_key_outside_the_assets_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor, area: str
    ) -> None:
        """Verify a key in the project's source area, its uploads or any other area is not found, not an error.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param area: Directory of the project the key lies in.
        :type area: str
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(
                fx_actor, StorageKey(f'{ProjectKeys(project.id).prefix}{area}/{uuid4()}')
            ):
                pytest.fail('A key outside the assets was opened.')

    async def test_key_without_a_file_is_not_found(
        self, fx_service: Callable[[], PageService], fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a key of the actor's project with nothing stored behind it is not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], PageService]
        :param fx_database: In-memory database of the test.
        :type fx_database: InMemoryDatabase
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(fx_actor, ProjectKeys(project.id).book):
                pytest.fail('A file that is not stored was opened.')
