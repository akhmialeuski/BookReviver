"""Tests for the IIIF file endpoint, on the SQLite file and the local stores under the test's data directory."""

import json
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.routers.iiif import IMMUTABLE_CACHE
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE, IIIF_INFO_MEDIA_TYPE
from bookreviver.domain.enums import Rendition
from bookreviver.domain.ids import JobId, SourceId, StorageKey
from bookreviver.domain.keys import BookKeys
from tests.helpers.builders import make_page, make_page_version, make_record
from tests.helpers.seeding import commit_account, store_book
from tests.helpers.storage import upload

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookRecord, PageVersion
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore, SourceStore
    from tests.helpers.database import UnitOfWorkFactory

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

    :ivar book_record: The record of the book.
    :ivar version: The base version of its page.
    """

    book_record: BookRecord
    version: PageVersion

    @property
    def pyramid(self) -> StorageKey:
        """The key of the directory of the tile pyramid of the page."""
        return BookKeys(self.book_record.id).version_rendition(self.version, Rendition.TILES)


async def _store_book(uow: UnitOfWork, assets: AssetStore, owner: AccountId) -> Book:
    """Store a book with a page whose pyramid holds an ``info.json`` and one tile, and whose thumbnail exists.

    :param uow: Unit of work over the catalogue and the books of the test.
    :type uow: UnitOfWork
    :param assets: Asset store of the application.
    :type assets: AssetStore
    :param owner: Committed account that owns the book.
    :type owner: AccountId
    :returns: The stored book.
    :rtype: Book
    """
    book_record = make_record()
    page = make_page()
    version = make_page_version(page_id=page.id)
    await store_book(uow, book_record, page, owner=owner, versions=[version])
    keys = BookKeys(book_record.id)
    for key, content in (
        (f'{keys.version_rendition(version, Rendition.TILES)}/{IIIF_INFO_FILE}', INFO_CONTENT),
        (f'{keys.version_rendition(version, Rendition.TILES)}/{TILE_PATH}', TILE_CONTENT),
        (keys.version_rendition(version, Rendition.THUMBNAIL), TILE_CONTENT),
    ):
        async with assets.writable(StorageKey(key)) as path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return Book(book_record=book_record, version=version)


class TestIiifFile:
    """Tests for GET /iiif/{key}."""

    async def test_info_json_names_the_address_it_was_asked_at(
        self,
        fx_client: httpx.AsyncClient,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: AssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the document keeps what the pyramid stores but its id, which is the address of the pyramid asked for.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Asset store of the application.
        :type fx_asset_store: AssetStore
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        book = await _store_book(fx_uow(), fx_asset_store, fx_actor.account_id)

        response = await fx_client.get(f'{IIIF_PATH}/{book.pyramid}/{IIIF_INFO_FILE}')

        expect(response.status_code == status.HTTP_200_OK)
        expect(response.json() == {**json.loads(INFO_CONTENT), 'id': f'{IIIF_PATH}/{book.pyramid}'})
        expect(response.headers[CONTENT_TYPE_HEADER] == IIIF_INFO_MEDIA_TYPE)
        expect(response.headers[CACHE_HEADER] == CACHE_VALUE)
        assert_expectations()

    @pytest.mark.parametrize('rendition', [Rendition.THUMBNAIL, Rendition.TILES], ids=['thumbnail', 'tile'])
    async def test_streams_other_files_unchanged_as_immutable(
        self,
        fx_client: httpx.AsyncClient,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: AssetStore,
        fx_actor: Actor,
        rendition: Rendition,
    ) -> None:
        """Verify a thumbnail, and a tile addressed like a IIIF viewer does with commas in its path, stream unchanged.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Asset store of the application.
        :type fx_asset_store: AssetStore
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param rendition: Rendition whose file is requested, a tile being a file inside the pyramid.
        :type rendition: Rendition
        """
        book = await _store_book(fx_uow(), fx_asset_store, fx_actor.account_id)
        key = BookKeys(book.book_record.id).version_rendition(book.version, rendition)
        address = f'{key}/{TILE_PATH}' if rendition is Rendition.TILES else key

        response = await fx_client.get(f'{IIIF_PATH}/{address}')

        expect(response.status_code == status.HTTP_200_OK)
        expect(response.content == TILE_CONTENT)
        expect(response.headers[CACHE_HEADER] == CACHE_VALUE)
        assert_expectations()

    @pytest.mark.parametrize('name', ['full.jpg', Rendition.TILES], ids=['missing-file', 'directory'])
    async def test_key_without_a_file_is_not_found(
        self,
        fx_client: httpx.AsyncClient,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: AssetStore,
        fx_actor: Actor,
        name: str,
    ) -> None:
        """Verify a key with nothing stored, and a key naming a directory, both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Asset store of the application.
        :type fx_asset_store: AssetStore
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param name: File or directory inside the version's directory that is requested.
        :type name: str
        """
        book = await _store_book(fx_uow(), fx_asset_store, fx_actor.account_id)
        directory = BookKeys(book.book_record.id).version_directory(book.version)

        response = await fx_client.get(f'{IIIF_PATH}/{directory}/{name}')

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_another_accounts_file_is_served(
        self,
        fx_client: httpx.AsyncClient,
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: AssetStore,
    ) -> None:
        """Verify a stored file of another account's book is served by its key alone, with no owner checked.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Asset store of the application.
        :type fx_asset_store: AssetStore
        """
        book = await _store_book(fx_uow(), fx_asset_store, await commit_account(fx_database))

        response = await fx_client.get(f'{IIIF_PATH}/{book.pyramid}/{IIIF_INFO_FILE}')

        assert response.status_code == status.HTTP_200_OK

    async def test_source_and_upload_files_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_source_store: SourceStore, fx_actor: Actor
    ) -> None:
        """Verify a stored source file and a staged upload of the account's own book answer 404, not 500.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_source_store: Source store of the application.
        :type fx_source_store: SourceStore
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        book_record = make_record()
        await store_book(fx_uow(), book_record, owner=fx_actor.account_id)
        keys = BookKeys(book_record.id)
        promoted, staged = JobId(uuid4()), JobId(uuid4())
        source_id = SourceId(uuid4())
        for job_id in (promoted, staged):
            await fx_source_store.stage(book_record.id, job_id, [upload(SOURCE_NAME)], max_bytes=MAX_UPLOAD_BYTES)
        await fx_source_store.promote(book_record.id, promoted, source_id, names=[SOURCE_NAME])

        responses = [
            await fx_client.get(f'{IIIF_PATH}/{keys.source(source_id)}/{SOURCE_NAME}'),
            await fx_client.get(f'{IIIF_PATH}/{keys.incoming(staged)}/{SOURCE_NAME}'),
        ]

        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 2

    async def test_dot_segment_is_rejected(
        self,
        fx_client: httpx.AsyncClient,
        fx_uow: UnitOfWorkFactory,
        fx_asset_store: AssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify a key climbing with an encoded ``..`` is refused by validation before any lookup.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_asset_store: Asset store of the application.
        :type fx_asset_store: AssetStore
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        book = await _store_book(fx_uow(), fx_asset_store, fx_actor.account_id)

        response = await fx_client.get(f'{IIIF_PATH}/{book.pyramid}/%2E%2E/{Rendition.THUMBNAIL}')

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
