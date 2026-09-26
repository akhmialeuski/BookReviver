"""Contract of the AssetStore port, run against every storage adapter registered in the conftest."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import StorageKey
from tests.adapters.storage.samples import WriterFailedError, abandon_write

if TYPE_CHECKING:
    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

PAGE_PREFIX: StorageKey = StorageKey('projects/book/pages/0')
FILE_KEY: StorageKey = StorageKey(f'{PAGE_PREFIX}/v1/full.jpg')
DIRECTORY_KEY: StorageKey = StorageKey(f'{PAGE_PREFIX}/v1/iiif')
SIBLING_KEY: StorageKey = StorageKey('projects/book/pages/1/v1/full.jpg')
TILE_NAME: str = 'info.json'
OLD_TILE_NAME: str = 'old.json'
OLD_CONTENT: bytes = b'old image'
NEW_CONTENT: bytes = b'new image'


async def _store_file(store: AssetStore, key: StorageKey, content: bytes) -> None:
    """Write one file at ``key``."""
    async with store.writable(key) as path:
        path.write_bytes(content)


async def _store_directory(store: AssetStore, key: StorageKey, name: str, content: bytes) -> None:
    """Write a directory holding one file at ``key``."""
    async with store.writable(key) as path:
        path.mkdir()
        (path / name).write_bytes(content)


async def _read_file(store: AssetStore, key: StorageKey) -> bytes:
    """Return the content of the file stored at ``key``."""
    async with store.readable(key) as path:
        return path.read_bytes()


async def _list_directory(store: AssetStore, key: StorageKey) -> dict[str, bytes]:
    """Return the files of the directory stored at ``key`` by name."""
    async with store.readable(key) as path:
        return {child.name: child.read_bytes() for child in path.iterdir()}


async def _is_stored(store: AssetStore, key: StorageKey) -> bool:
    """Return whether anything is stored at ``key``."""
    try:
        async with store.readable(key):
            return True
    except NotFoundError:
        return False


class TestWritable:
    """Contract of AssetStore.writable()."""

    async def test_publishes_file_only_on_exit(self, fx_asset_store: AssetStore) -> None:
        """Verify a file being written is invisible to readers until the writer finishes."""
        async with fx_asset_store.writable(FILE_KEY) as path:
            path.write_bytes(NEW_CONTENT)
            expect(not await _is_stored(fx_asset_store, FILE_KEY))

        expect(await _read_file(fx_asset_store, FILE_KEY) == NEW_CONTENT)
        assert_expectations()

    async def test_publishes_directory_only_on_exit(self, fx_asset_store: AssetStore) -> None:
        """Verify a pyramid being cut is invisible to readers until the writer finishes."""
        async with fx_asset_store.writable(DIRECTORY_KEY) as path:
            path.mkdir()
            (path / TILE_NAME).write_bytes(NEW_CONTENT)
            expect(not await _is_stored(fx_asset_store, DIRECTORY_KEY))

        expect(await _list_directory(fx_asset_store, DIRECTORY_KEY) == {TILE_NAME: NEW_CONTENT})
        assert_expectations()

    async def test_replaces_stored_file(self, fx_asset_store: AssetStore) -> None:
        """Verify writing a key again replaces its file."""
        await _store_file(fx_asset_store, FILE_KEY, OLD_CONTENT)

        await _store_file(fx_asset_store, FILE_KEY, NEW_CONTENT)

        assert await _read_file(fx_asset_store, FILE_KEY) == NEW_CONTENT

    async def test_replaces_stored_directory_whole(self, fx_asset_store: AssetStore) -> None:
        """Verify writing a directory key again leaves none of the old directory's files."""
        await _store_directory(fx_asset_store, DIRECTORY_KEY, OLD_TILE_NAME, OLD_CONTENT)

        await _store_directory(fx_asset_store, DIRECTORY_KEY, TILE_NAME, NEW_CONTENT)

        assert await _list_directory(fx_asset_store, DIRECTORY_KEY) == {TILE_NAME: NEW_CONTENT}

    async def test_failed_write_keeps_stored_file(self, fx_asset_store: AssetStore) -> None:
        """Verify a writer that fails publishes nothing and the previous file stays readable."""
        await _store_file(fx_asset_store, FILE_KEY, OLD_CONTENT)

        with pytest.raises(WriterFailedError):
            await abandon_write(fx_asset_store, FILE_KEY, NEW_CONTENT)

        assert await _read_file(fx_asset_store, FILE_KEY) == OLD_CONTENT


class TestReadable:
    """Contract of AssetStore.readable()."""

    async def test_missing_key_raises_not_found(self, fx_asset_store: AssetStore) -> None:
        """Verify reading a key nothing was written to raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await _read_file(fx_asset_store, FILE_KEY)


class TestDeletePrefix:
    """Contract of AssetStore.delete_prefix()."""

    async def test_removes_everything_under_prefix_only(self, fx_asset_store: AssetStore) -> None:
        """Verify files and directories under the prefix are gone and a key outside it stays."""
        await _store_file(fx_asset_store, FILE_KEY, NEW_CONTENT)
        await _store_directory(fx_asset_store, DIRECTORY_KEY, TILE_NAME, NEW_CONTENT)
        await _store_file(fx_asset_store, SIBLING_KEY, NEW_CONTENT)

        await fx_asset_store.delete_prefix(PAGE_PREFIX)

        expect(not await _is_stored(fx_asset_store, FILE_KEY))
        expect(not await _is_stored(fx_asset_store, DIRECTORY_KEY))
        expect(await _is_stored(fx_asset_store, SIBLING_KEY))
        assert_expectations()

    async def test_missing_prefix_is_not_an_error(self, fx_asset_store: AssetStore) -> None:
        """Verify deleting a prefix with nothing under it succeeds, so cleanup can always run."""
        await fx_asset_store.delete_prefix(PAGE_PREFIX)

        assert not await _is_stored(fx_asset_store, FILE_KEY)
