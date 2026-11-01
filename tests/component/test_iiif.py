"""Tests for the IIIF file endpoint, on the SQLite file and the local stores under the test's data directory."""

import json
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.routers.iiif import IMMUTABLE_CACHE
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE, IIIF_INFO_MEDIA_TYPE
from bookreviver.domain.ids import JobId, SourceId, StorageKey
from bookreviver.domain.images import Rendition
from bookreviver.domain.keys import BookKeys
from tests.harness.adapters.storage import upload
from tests.harness.builders.domain import make_page, make_page_version, make_record
from tests.harness.builders.seeding import commit_account, store_book

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import AccountId, BookId
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import CatalogueDatabase
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases
    from tests.harness.adapters.storage import SharedBookDirectories
    from tests.harness.builders.domain import BookOpener

pytestmark = pytest.mark.anyio

IIIF_PATH: str = '/api/v1/iiif'
CACHE_HEADER, CACHE_VALUE = next(iter(IMMUTABLE_CACHE.items()))
CONTENT_TYPE_HEADER: str = 'content-type'
TILE_PATH: str = '0,0,512,512/512,/0/default.jpg'
TILE_CONTENT: bytes = b'\xff\xd8 tile'
# What a pyramid stores: an id that names another address, as one cut for a book under another identifier does
INFO_CONTENT: bytes = (
    b'{"@context":"http://iiif.io/api/image/3/context.json","id":"/api/v1/iiif/elsewhere","type":"ImageService3"}'
)
SOURCE_NAME: str = 'book.pdf'
MAX_UPLOAD_BYTES: int = 1024


class Book(NamedTuple):
    """A book of the signed-in account with one page whose base version has files in the asset store.

    :ivar book_id: Identifier of the book.
    :ivar version: The base version of its page.
    """

    book_id: BookId
    version: PageVersion

    @property
    def pyramid(self) -> StorageKey:
        """The key of the directory of the tile pyramid of the page."""
        return BookKeys.version_rendition(self.version, Rendition.TILES)


async def _store_book(
    catalogue: CatalogueDatabase, files: SharedBookDirectories, owner: AccountId, *, databases: LocatedBookDatabases
) -> Book:
    """Store a book with a page whose pyramid holds an ``info.json`` and one tile, and whose thumbnail exists.

    :param catalogue: Catalogue database over the catalogue and the books of the test.
    :type catalogue: CatalogueDatabase
    :param files: Opens the asset store of the book, the application's own.
    :type files: SharedBookDirectories
    :param owner: Committed account that owns the book.
    :type owner: AccountId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The stored book.
    :rtype: Book
    """
    page = make_page()
    version = make_page_version(page_id=page.id)
    book_id = await store_book(catalogue, make_record(), page, owner=owner, versions=[version], databases=databases)
    for key, content in (
        (f'{BookKeys.version_rendition(version, Rendition.TILES)}/{IIIF_INFO_FILE}', INFO_CONTENT),
        (f'{BookKeys.version_rendition(version, Rendition.TILES)}/{TILE_PATH}', TILE_CONTENT),
        (BookKeys.version_rendition(version, Rendition.THUMBNAIL), TILE_CONTENT),
    ):
        async with files.assets(book_id).writable(StorageKey(key)) as path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return Book(book_id=book_id, version=version)


class TestIiifFile:
    """Tests for GET /iiif/{book_id}/{key}."""

    async def test_info_json_names_the_address_it_was_asked_at(
        self,
        fx_client: httpx.AsyncClient,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the document keeps what the pyramid stores but its id, which is the address of the pyramid asked for.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = await _store_book(fx_catalogue(), fx_directories, fx_actor.account_id, databases=fx_databases())

        response = await fx_client.get(f'{IIIF_PATH}/{book.book_id}/{book.pyramid}/{IIIF_INFO_FILE}')

        expect(response.status_code == status.HTTP_200_OK)
        expect(response.json() == {**json.loads(INFO_CONTENT), 'id': f'{IIIF_PATH}/{book.book_id}/{book.pyramid}'})
        expect(response.headers[CONTENT_TYPE_HEADER] == IIIF_INFO_MEDIA_TYPE)
        expect(response.headers[CACHE_HEADER] == CACHE_VALUE)
        assert_expectations()

    @pytest.mark.parametrize('rendition', [Rendition.THUMBNAIL, Rendition.TILES], ids=['thumbnail', 'tile'])
    async def test_streams_other_files_unchanged_as_immutable(
        self,
        fx_client: httpx.AsyncClient,
        fx_directories: SharedBookDirectories,
        fx_actor: Actor,
        rendition: Rendition,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a thumbnail, and a tile addressed like a IIIF viewer does with commas in its path, stream unchanged.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param rendition: Rendition whose file is requested, a tile being a file inside the pyramid.
        :type rendition: Rendition
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book = await _store_book(
            fx_open_book.catalogue(), fx_directories, fx_actor.account_id, databases=fx_open_book.databases
        )
        key = BookKeys.version_rendition(book.version, rendition)
        address = f'{key}/{TILE_PATH}' if rendition is Rendition.TILES else key

        response = await fx_client.get(f'{IIIF_PATH}/{book.book_id}/{address}')

        expect(response.status_code == status.HTTP_200_OK)
        expect(response.content == TILE_CONTENT)
        expect(response.headers[CACHE_HEADER] == CACHE_VALUE)
        assert_expectations()

    @pytest.mark.parametrize('name', ['full.jpg', Rendition.TILES], ids=['missing-file', 'directory'])
    async def test_key_without_a_file_is_not_found(
        self,
        fx_client: httpx.AsyncClient,
        fx_directories: SharedBookDirectories,
        fx_actor: Actor,
        name: str,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a key with nothing stored, and a key naming a directory, both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param name: File or directory inside the version's directory that is requested.
        :type name: str
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book = await _store_book(
            fx_open_book.catalogue(), fx_directories, fx_actor.account_id, databases=fx_open_book.databases
        )
        directory = BookKeys.version_directory(book.version)

        response = await fx_client.get(f'{IIIF_PATH}/{book.book_id}/{directory}/{name}')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_another_accounts_file_is_served(
        self,
        fx_client: httpx.AsyncClient,
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a stored file of another account's book is served by its key alone, with no owner checked.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = await _store_book(
            fx_catalogue(), fx_directories, await commit_account(fx_database), databases=fx_databases()
        )

        response = await fx_client.get(f'{IIIF_PATH}/{book.book_id}/{book.pyramid}/{IIIF_INFO_FILE}')

        assert response.status_code == status.HTTP_200_OK

    async def test_source_and_upload_files_are_not_found(
        self,
        fx_client: httpx.AsyncClient,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a stored source file and a staged upload of the account's own book answer 404, not 500.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())
        promoted, staged = JobId(uuid4()), JobId(uuid4())
        source_id = SourceId(uuid4())
        for job_id in (promoted, staged):
            await fx_directories.sources(book_id).stage(job_id, [upload(SOURCE_NAME)], max_bytes=MAX_UPLOAD_BYTES)
        await fx_directories.sources(book_id).promote(promoted, source_id, names=[SOURCE_NAME])

        responses = [
            await fx_client.get(f'{IIIF_PATH}/{book_id}/{BookKeys.source(source_id)}/{SOURCE_NAME}'),
            await fx_client.get(f'{IIIF_PATH}/{book_id}/{BookKeys.incoming(staged)}/{SOURCE_NAME}'),
        ]

        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 2

    async def test_dot_segment_is_rejected(
        self,
        fx_client: httpx.AsyncClient,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a key climbing with an encoded ``..`` is refused by validation before any lookup.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = await _store_book(fx_catalogue(), fx_directories, fx_actor.account_id, databases=fx_databases())

        response = await fx_client.get(f'{IIIF_PATH}/{book.book_id}/{book.pyramid}/%2E%2E/{Rendition.THUMBNAIL}')

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
