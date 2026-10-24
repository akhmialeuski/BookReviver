"""Tests for the source and scan endpoints, on the SQLite file and the local stores under the data directory."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.api.schemas.pages import PageSchema
from bookreviver.api.schemas.sources import ScanSchema, SourceSchema
from bookreviver.domain.enums import (
    FileType,
    ImagePolicy,
    JobKind,
    RejectionReason,
    Rendition,
    SourceKind,
    SourceStatus,
)
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import Renditions
from tests.helpers.books import IMAGE, SCANS_PER_SOURCE, commit_book, on_disk
from tests.helpers.builders import EPOCH, make_job, make_record, make_scan, make_source
from tests.helpers.clock import FixedClock
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fakes_jobs import FakeBoardProvider, RecordingEventBus
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import httpx
    from dishka import Provider

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, Source
    from bookreviver.ports.persistence import BookDatabases
    from tests.helpers.books import Book
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

STATUS_PARAM: str = 'status'
BOOKS_PATH: str = '/api/v1/books'
IIIF_PATH: str = '/api/v1/iiif'
PAGE_SIZE_PARAM: str = 'size'
PAGE_NUMBER_PARAM: str = 'page'
SOURCE_ID_PARAM: str = 'source_id'


@pytest.fixture
async def fx_book(
    fx_database: SqlDatabase,
    fx_actor: Actor,
    fx_open_book: BookOpener,
) -> Book:
    """Commit and store a book of the signed-in account.

    :param fx_database: Database of the test, which the application runs on.
    :type fx_database: SqlDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_open_book, fx_actor)


@pytest.fixture
async def fx_strangers_book(
    fx_database: SqlDatabase,
    fx_open_book: BookOpener,
) -> Book:
    """Commit and store a book of an account that is not signed in.

    :param fx_database: Database of the test, which the application runs on.
    :type fx_database: SqlDatabase
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The stored book.
    :rtype: Book
    """
    return await commit_book(fx_database, fx_open_book, None)


def _sources_path(book: Book) -> str:
    """Return the path of the book's list of sources.

    :param book: The stored book.
    :type book: Book
    :returns: The path below the API prefix.
    :rtype: str
    """
    return f'{BOOKS_PATH}/{book.book_record.id}/sources'


@pytest.fixture
def fx_board() -> FakeJobBoard:
    """Build the board the application of these tests puts its jobs on, which runs none until a test says so.

    :returns: A fake board with no job.
    :rtype: FakeJobBoard
    """
    return FakeJobBoard(runner=ScriptedRunner(), publisher=RecordingEventBus(), clock=FixedClock(EPOCH))


@pytest.fixture
def fx_extra_providers(fx_board: FakeJobBoard) -> Sequence[Provider]:
    """Replace the application's job board with ``fx_board``, so a test can hold an import active.

    :param fx_board: Board of the test.
    :type fx_board: FakeJobBoard
    :returns: One provider that supplies the board.
    :rtype: Sequence[Provider]
    """
    return (FakeBoardProvider(fx_board),)


async def _add_unimported(
    book: Book, status_of: SourceStatus, name: str, *, minutes: int, databases: BookDatabases
) -> Source:
    """Commit a file of the book that the import rejected or skipped, which has no scan and no stored file.

    :param book: The stored book.
    :type book: Book
    :param status_of: Status of the file, rejected or skipped.
    :type status_of: SourceStatus
    :param name: Name of the file.
    :type name: str
    :param minutes: Minutes after the epoch the file was recorded.
    :type minutes: int
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The stored source.
    :rtype: Source
    """
    rejected = status_of is SourceStatus.REJECTED
    source = evolve(
        make_source(name=name, minutes=minutes),
        kind=None,
        file_type=None,
        scan_count=0,
        status=status_of,
        rejection=RejectionReason.UNSUPPORTED_TYPE if rejected else None,
        detail=RejectionReason.UNSUPPORTED_TYPE.label if rejected else '',
    )
    await databases.open(book.book_record.id).change(lambda repos, _book_record: repos.sources.add(source))
    return source


class TestListSources:
    """Tests for GET /books/{book_id}/sources."""

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
        scans_of_first = sum(scan.source_id == fx_book.first.id for scan in fx_book.scans)
        expect((first.size_bytes, first.sha256, first.scan_count) == (4096, fx_book.first.sha256, scans_of_first))
        expect((first.metadata, first.suggestion.title) == ({}, ''))
        expect((first.status, first.rejection, first.detail) == (SourceStatus.IMPORTED, None, ''))
        expect(sources.total == 2)
        assert_expectations()

    async def test_lists_the_imported_sources_alone_unless_asked_for_the_report_of_an_import(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the rejected and skipped files are listed only for their statuses, with their reasons.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        rejected = await _add_unimported(
            fx_book, SourceStatus.REJECTED, 'notes.txt', minutes=5, databases=fx_databases()
        )
        skipped = await _add_unimported(fx_book, SourceStatus.SKIPPED, 'later.pdf', minutes=6, databases=fx_databases())

        default = Page[SourceSchema].model_validate_json((await fx_client.get(_sources_path(fx_book))).content)
        report = Page[SourceSchema].model_validate_json(
            (
                await fx_client.get(
                    _sources_path(fx_book),
                    params=[(STATUS_PARAM, SourceStatus.REJECTED), (STATUS_PARAM, SourceStatus.SKIPPED)],
                )
            ).content
        )

        expect([item.id for item in default.items] == [fx_book.first.id, fx_book.second.id])
        expect([item.id for item in report.items] == [rejected.id, skipped.id])
        expect(
            [(item.status, item.rejection) for item in report.items]
            == [
                (SourceStatus.REJECTED, RejectionReason.UNSUPPORTED_TYPE),
                (SourceStatus.SKIPPED, None),
            ]
        )
        expect([(item.kind, item.file_type) for item in report.items] == [(None, None), (None, None)])
        expect(report.items[0].detail == RejectionReason.UNSUPPORTED_TYPE.label)
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

    async def test_another_accounts_book_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_strangers_book: Book
    ) -> None:
        """Verify the sources of another account's book cannot be listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        """
        response = await fx_client.get(_sources_path(fx_strangers_book))

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestGetSource:
    """Tests for GET /books/{book_id}/sources/{source_id}."""

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
        """Verify an identifier no source has and a source of another account's book both answer 404.

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
    """Tests for DELETE /books/{book_id}/sources/{source_id}."""

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
            (await fx_client.get(f'{BOOKS_PATH}/{fx_book.book_record.id}/pages/{fx_book.page.id}')).content
        )
        assert page.images is not None
        image = await fx_client.get(page.images.full)
        sources = Page[SourceSchema].model_validate_json((await fx_client.get(_sources_path(fx_book))).content)
        scans = Page[ScanSchema].model_validate_json(
            (await fx_client.get(f'{BOOKS_PATH}/{fx_book.book_record.id}/scans')).content
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
        self, fx_client: httpx.AsyncClient, fx_board: FakeJobBoard, fx_book: Book, fx_storage_root: Path
    ) -> None:
        """Verify a source cannot be deleted while the book imports, and keeps its files.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_board: Board of the application.
        :type fx_board: FakeJobBoard
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_storage_root: Storage root of the application.
        :type fx_storage_root: Path
        """
        fx_board.put(make_job(book_id=fx_book.book_record.id, kind=JobKind.IMPORT_SOURCE))

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


class TestDeleteUnimportedSources:
    """Tests for DELETE /books/{book_id}/sources/unimported."""

    async def test_deletes_the_report_of_the_import_and_keeps_the_imported_sources(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify closing the report answers 204 and deletes the rejected and skipped files, and nothing else.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        await _add_unimported(fx_book, SourceStatus.REJECTED, 'notes.txt', minutes=5, databases=fx_databases())
        await _add_unimported(fx_book, SourceStatus.SKIPPED, 'later.pdf', minutes=6, databases=fx_databases())

        response = await fx_client.delete(f'{_sources_path(fx_book)}/unimported')

        everything = Page[SourceSchema].model_validate_json(
            (
                await fx_client.get(
                    _sources_path(fx_book), params=[(STATUS_PARAM, status_name) for status_name in SourceStatus]
                )
            ).content
        )
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect([item.id for item in everything.items] == [fx_book.first.id, fx_book.second.id])
        assert_expectations()

    async def test_import_in_progress_is_a_conflict(
        self, fx_client: httpx.AsyncClient, fx_board: FakeJobBoard, fx_book: Book, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the report of an import that still runs is not closed, and its rows stay.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_board: Board of the application.
        :type fx_board: FakeJobBoard
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        kept = await _add_unimported(fx_book, SourceStatus.REJECTED, 'notes.txt', minutes=5, databases=fx_databases())
        fx_board.put(make_job(book_id=fx_book.book_record.id, kind=JobKind.IMPORT_SOURCE))

        response = await fx_client.delete(f'{_sources_path(fx_book)}/unimported')

        report = Page[SourceSchema].model_validate_json(
            (await fx_client.get(_sources_path(fx_book), params=[(STATUS_PARAM, SourceStatus.REJECTED)])).content
        )
        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect([item.id for item in report.items] == [kept.id])
        assert_expectations()

    async def test_another_accounts_book_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_strangers_book: Book
    ) -> None:
        """Verify the report of another account's book cannot be closed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        """
        response = await fx_client.delete(f'{_sources_path(fx_strangers_book)}/unimported')

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestListScans:
    """Tests for GET /books/{book_id}/scans."""

    async def test_lists_the_scans_of_the_book_with_paths_of_their_images(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the scans come source by source with image paths that have no scheme or host.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{BOOKS_PATH}/{fx_book.book_record.id}/scans')

        scans = Page[ScanSchema].model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect([item.id for item in scans.items] == [scan.id for scan in fx_book.scans])
        expect(scans.total == len(fx_book.scans))
        expect(all(item.images is not None for item in scans.items))
        prefix = f'{IIIF_PATH}/{fx_book.book_record.id}/assets/'
        expect(all(item.images.full.startswith(prefix) for item in scans.items if item.images))
        assert_expectations()

    @pytest.mark.parametrize('full', [Rendition.FULL_JPEG, Rendition.FULL_PNG])
    async def test_full_image_path_has_the_extension_of_the_format_recorded_with_the_scan(
        self,
        fx_client: httpx.AsyncClient,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        full: Rendition,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the path of the full image names the format its scan was stored in, whatever the book's policy.

        The book's policy is the opposite of what the scan records, as after a change of the policy.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param full: Format the scan records for its full image.
        :type full: Rendition
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        policy = ImagePolicy.LOSSLESS if full is Rendition.FULL_JPEG else ImagePolicy.COMPACT
        book_record = evolve(make_record(), image_policy=policy)
        source = make_source()
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True, full=full))
        await store_book(
            fx_uow(), book_record, owner=fx_actor.account_id, sources=[source], scans=[scan], databases=fx_databases()
        )

        response = await fx_client.get(f'{BOOKS_PATH}/{book_record.id}/scans')

        images = Page[ScanSchema].model_validate_json(response.content).items[0].images
        assert images is not None
        assert images.full == f'{IIIF_PATH}/{book_record.id}/{BookKeys().scan_rendition(scan, full)}'

    async def test_scan_without_ready_renditions_has_no_images(
        self,
        fx_client: httpx.AsyncClient,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a scan whose renditions are not cut yet is listed without image paths.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        source = make_source()
        await store_book(
            fx_uow(),
            book_record,
            owner=fx_actor.account_id,
            sources=[source],
            scans=[make_scan(source=source, number=0)],
            databases=fx_databases(),
        )

        response = await fx_client.get(f'{BOOKS_PATH}/{book_record.id}/scans')

        assert Page[ScanSchema].model_validate_json(response.content).items[0].images is None

    async def test_filters_by_source_in_windows(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the scans of one source are listed by number in windows, with the source's count in the total.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(
            f'{BOOKS_PATH}/{fx_book.book_record.id}/scans',
            params={SOURCE_ID_PARAM: str(fx_book.second.id), PAGE_SIZE_PARAM: 2, PAGE_NUMBER_PARAM: 2},
        )

        scans = Page[ScanSchema].model_validate_json(response.content)
        expect([(item.source_id, item.number) for item in scans.items] == [(fx_book.second.id, 2)])
        expect((scans.total, scans.pages) == (SCANS_PER_SOURCE, 2))
        assert_expectations()

    async def test_unknown_source_and_another_accounts_book_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_strangers_book: Book
    ) -> None:
        """Verify a source filter that names no source of the book, and a stranger's book, both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_strangers_book: Book of another account.
        :type fx_strangers_book: Book
        """
        unknown = await fx_client.get(
            f'{BOOKS_PATH}/{fx_book.book_record.id}/scans', params={SOURCE_ID_PARAM: str(uuid4())}
        )
        foreign = await fx_client.get(f'{BOOKS_PATH}/{fx_strangers_book.book_record.id}/scans')

        assert [unknown.status_code, foreign.status_code] == [status.HTTP_404_NOT_FOUND] * 2

    async def test_malformed_source_filter_is_invalid(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a source filter that is no identifier is refused by validation.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(
            f'{BOOKS_PATH}/{fx_book.book_record.id}/scans', params={SOURCE_ID_PARAM: 'not-an-identifier'}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
