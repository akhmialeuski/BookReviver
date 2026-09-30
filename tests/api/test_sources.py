"""Tests for the source and scan endpoints, on in-memory persistence and the local stores under the data directory."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.api.schemas.sources import ScanSchema, SourceSchema
from bookreviver.domain.enums import FileType, ImagePolicy, JobState, Rendition, SourceKind
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Renditions
from tests.helpers.books import IMAGE, SCANS_PER_SOURCE, commit_book, on_disk
from tests.helpers.builders import make_job, make_project, make_scan, make_source
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor
    from bookreviver.ports.storage import AssetStore, SourceStore
    from tests.helpers.books import Book

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
IIIF_PATH: str = '/api/v1/iiif'
PAGE_SIZE_PARAM: str = 'size'
PAGE_NUMBER_PARAM: str = 'page'
SOURCE_ID_PARAM: str = 'source_id'


@pytest.fixture
async def fx_book(
    fx_database: InMemoryDatabase, fx_source_store: SourceStore, fx_asset_store: AssetStore, fx_actor: Actor
) -> Book:
    """Commit and store a book of the signed-in account.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_source_store: Source store of the application.
    :type fx_source_store: SourceStore
    :param fx_asset_store: Asset store of the application.
    :type fx_asset_store: AssetStore
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_source_store, fx_asset_store, fx_actor)


@pytest.fixture
async def fx_strangers_book(
    fx_database: InMemoryDatabase, fx_source_store: SourceStore, fx_asset_store: AssetStore
) -> Book:
    """Commit and store a book of an account that is not signed in.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_source_store: Source store of the application.
    :type fx_source_store: SourceStore
    :param fx_asset_store: Asset store of the application.
    :type fx_asset_store: AssetStore
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_source_store, fx_asset_store, None)


def _sources_path(book: Book) -> str:
    """Return the path of the book's list of sources.

    :param book: The stored book.
    :type book: Book
    :returns: The path below the API prefix.
    :rtype: str
    """
    return f'{PROJECTS_PATH}/{book.project.id}/sources'


async def _add_job(database: InMemoryDatabase, book: Book, state: JobState) -> None:
    """Commit an import job of the book's project.

    :param database: In-memory database of the application.
    :type database: InMemoryDatabase
    :param book: The stored book.
    :type book: Book
    :param state: State of the job.
    :type state: JobState
    """
    uow = InMemoryUnitOfWork(database)
    await uow.jobs.add(make_job(project_id=book.project.id, state=state))
    await uow.commit()


class TestListSources:
    """Tests for GET /projects/{project_id}/sources."""

    async def test_lists_the_sources_in_import_order_with_their_files(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the sources come earliest import first with their kind, files, metadata and suggestion.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(_sources_path(fx_book))

        sources = Page[SourceSchema].model_validate_json(response.content)
        first = sources.items[0]
        expect(response.status_code == status.HTTP_200_OK)
        expect([item.id for item in sources.items] == [fx_book.first.id, fx_book.second.id])
        expect((first.kind, first.file_type, first.file_name) == (SourceKind.PDF, FileType.PDF, 'part1.pdf'))
        expect([file.name for file in first.files] == ['part1.pdf'])
        expect((first.size_bytes, first.sha256, first.scan_count) == (4096, fx_book.first.sha256, 12))
        expect((first.metadata, first.suggestion.title, first.import_job_id) == ({}, '', None))
        expect(sources.total == 2)
        assert_expectations()

    async def test_pages_through_the_sources(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the second window of one source holds the second source.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(_sources_path(fx_book), params={PAGE_SIZE_PARAM: 1, PAGE_NUMBER_PARAM: 2})

        sources = Page[SourceSchema].model_validate_json(response.content)
        expect([item.id for item in sources.items] == [fx_book.second.id])
        expect((sources.total, sources.pages) == (2, 2))
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_strangers_book: Book
    ) -> None:
        """Verify the sources of another account's project cannot be listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        """
        response = await fx_client.get(_sources_path(fx_strangers_book))

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestGetSource:
    """Tests for GET /projects/{project_id}/sources/{source_id}."""

    async def test_returns_one_source(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a source is read by its identifier.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{_sources_path(fx_book)}/{fx_book.second.id}')

        expect(response.status_code == status.HTTP_200_OK)
        expect(SourceSchema.model_validate_json(response.content).id == fx_book.second.id)
        assert_expectations()

    async def test_missing_source_and_a_strangers_source_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_strangers_book: Book
    ) -> None:
        """Verify an identifier no source has and a source of another account's project both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        """
        paths = [
            f'{_sources_path(fx_book)}/{uuid4()}',
            f'{_sources_path(fx_book)}/{fx_strangers_book.first.id}',
            f'{_sources_path(fx_strangers_book)}/{fx_strangers_book.first.id}',
        ]

        responses = [await fx_client.get(path) for path in paths]

        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 3


class TestDeleteSource:
    """Tests for DELETE /projects/{project_id}/sources/{source_id}."""

    async def test_deletes_the_source_and_leaves_the_page_with_its_image(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_storage_root: Path
    ) -> None:
        """Verify the answer is 204, the source and its files are gone, and the book's page still shows its image.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_storage_root: Storage root of the application.
        :type fx_storage_root: Path
        """
        response = await fx_client.delete(f'{_sources_path(fx_book)}/{fx_book.first.id}')

        page = PageSchema.model_validate_json(
            (await fx_client.get(f'{PROJECTS_PATH}/{fx_book.project.id}/pages/{fx_book.page.id}')).content
        )
        assert page.images is not None
        image = await fx_client.get(page.images.full)
        sources = Page[SourceSchema].model_validate_json((await fx_client.get(_sources_path(fx_book))).content)
        scans = Page[ScanSchema].model_validate_json(
            (await fx_client.get(f'{PROJECTS_PATH}/{fx_book.project.id}/scans')).content
        )
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (False, False))
        expect(on_disk(fx_storage_root, fx_book, fx_book.second) == (True, True))
        expect([item.id for item in sources.items] == [fx_book.second.id])
        expect(scans.total == SCANS_PER_SOURCE)
        expect((page.scan_id, page.position) == (None, 0))
        expect((image.status_code, image.content) == (status.HTTP_200_OK, IMAGE))
        assert_expectations()

    async def test_deleting_twice_is_not_found_the_second_time(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a source that is gone answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        path = f'{_sources_path(fx_book)}/{fx_book.first.id}'
        await fx_client.delete(path)

        assert (await fx_client.delete(path)).status_code == status.HTTP_404_NOT_FOUND

    async def test_import_in_progress_is_a_conflict_and_removes_nothing(
        self,
        fx_client: httpx.AsyncClient,
        fx_database: InMemoryDatabase,
        fx_book: Book,
        fx_storage_root: Path,
    ) -> None:
        """Verify a source cannot be deleted while the project imports, and keeps its files.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_storage_root: Storage root of the application.
        :type fx_storage_root: Path
        """
        await _add_job(fx_database, fx_book, JobState.RUNNING)

        response = await fx_client.delete(f'{_sources_path(fx_book)}/{fx_book.first.id}')

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(on_disk(fx_storage_root, fx_book, fx_book.first) == (True, True))
        assert_expectations()

    async def test_another_accounts_source_is_not_found_and_kept(
        self, fx_client: httpx.AsyncClient, fx_strangers_book: Book, fx_storage_root: Path
    ) -> None:
        """Verify another account's source cannot be deleted and keeps its files.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        :param fx_storage_root: Storage root of the application.
        :type fx_storage_root: Path
        """
        response = await fx_client.delete(f'{_sources_path(fx_strangers_book)}/{fx_strangers_book.first.id}')

        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(on_disk(fx_storage_root, fx_strangers_book, fx_strangers_book.first) == (True, True))
        assert_expectations()


class TestListScans:
    """Tests for GET /projects/{project_id}/scans."""

    async def test_lists_the_scans_of_the_project_with_paths_of_their_images(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the scans come source by source with image paths that have no scheme or host.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{PROJECTS_PATH}/{fx_book.project.id}/scans')

        scans = Page[ScanSchema].model_validate_json(response.content)
        keys = ProjectKeys(fx_book.project.id)
        expect(response.status_code == status.HTTP_200_OK)
        expect([item.id for item in scans.items] == [scan.id for scan in fx_book.scans])
        expect(scans.total == len(fx_book.scans))
        expect(all(item.images is not None for item in scans.items))
        expect(all(item.images.full.startswith(f'{IIIF_PATH}/{keys.prefix}') for item in scans.items if item.images))
        assert_expectations()

    @pytest.mark.parametrize('full', [Rendition.FULL_JPEG, Rendition.FULL_PNG])
    async def test_full_image_path_has_the_extension_of_the_format_recorded_with_the_scan(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor, full: Rendition
    ) -> None:
        """Verify the path of the full image names the format its scan was stored in, whatever the project's policy.

        The project's policy is the opposite of what the scan records, as after a change of the policy.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param full: Format the scan records for its full image.
        :type full: Rendition
        """
        policy = ImagePolicy.LOSSLESS if full is Rendition.FULL_JPEG else ImagePolicy.COMPACT
        project = evolve(make_project(owner_id=fx_actor.account_id), image_policy=policy)
        source = make_source(project_id=project.id)
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True, full=full))
        await commit_project(fx_database, project, sources=[source], scans=[scan])

        response = await fx_client.get(f'{PROJECTS_PATH}/{project.id}/scans')

        images = Page[ScanSchema].model_validate_json(response.content).items[0].images
        assert images is not None
        assert images.full == f'{IIIF_PATH}/{ProjectKeys(project.id).scan_rendition(scan, full)}'

    async def test_scan_without_ready_renditions_has_no_images(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a scan whose renditions are not cut yet is listed without image paths.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        source = make_source(project_id=project.id)
        await commit_project(fx_database, project, sources=[source], scans=[make_scan(source=source, number=0)])

        response = await fx_client.get(f'{PROJECTS_PATH}/{project.id}/scans')

        assert Page[ScanSchema].model_validate_json(response.content).items[0].images is None

    async def test_filters_by_source_in_windows(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the scans of one source are listed by number in windows, with the source's count in the total.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(
            f'{PROJECTS_PATH}/{fx_book.project.id}/scans',
            params={SOURCE_ID_PARAM: str(fx_book.second.id), PAGE_SIZE_PARAM: 2, PAGE_NUMBER_PARAM: 2},
        )

        scans = Page[ScanSchema].model_validate_json(response.content)
        expect([(item.source_id, item.number) for item in scans.items] == [(fx_book.second.id, 2)])
        expect((scans.total, scans.pages) == (SCANS_PER_SOURCE, 2))
        assert_expectations()

    async def test_unknown_source_and_another_accounts_project_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_strangers_book: Book
    ) -> None:
        """Verify a source filter that names no source of the project, and a stranger's project, both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        """
        unknown = await fx_client.get(
            f'{PROJECTS_PATH}/{fx_book.project.id}/scans', params={SOURCE_ID_PARAM: str(uuid4())}
        )
        foreign = await fx_client.get(f'{PROJECTS_PATH}/{fx_strangers_book.project.id}/scans')

        assert [unknown.status_code, foreign.status_code] == [status.HTTP_404_NOT_FOUND] * 2

    async def test_malformed_source_filter_is_invalid(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a source filter that is no identifier is refused by validation.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(
            f'{PROJECTS_PATH}/{fx_book.project.id}/scans', params={SOURCE_ID_PARAM: 'not-an-identifier'}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
