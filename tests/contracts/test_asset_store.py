"""Contract of the AssetStore port, run against every storage backend the application registers."""

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import StorageKey
from tests.helpers.storage import WriterFailedError, abandon_write

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

PAGE_PREFIX: StorageKey = StorageKey('projects/book/pages/0')
FILE_KEY: StorageKey = StorageKey(f'{PAGE_PREFIX}/v1/full.jpg')
DIRECTORY_KEY: StorageKey = StorageKey(f'{PAGE_PREFIX}/v1/iiif')
# Shares the prefix as a string but not as a path segment, so it must survive deleting the prefix
SIBLING_KEY: StorageKey = StorageKey('projects/book/pages/01/v1/full.jpg')
TILE_NAME: str = 'info.json'
CASE_ARG: str = 'case'
KEY_ARG: str = 'key'
OLD_CONTENT: bytes = b'old image'
NEW_CONTENT: bytes = b'new image'


def _fill_file(path: Path, content: bytes) -> None:
    """Make ``path`` a file with ``content``.

    :param path: Path a writer was given, which does not exist yet.
    :type path: Path
    :param content: Bytes of the file.
    :type content: bytes
    """
    path.write_bytes(content)


def _fill_directory(path: Path, content: bytes) -> None:
    """Make ``path`` a directory holding one tile file with ``content``.

    :param path: Path a writer was given, which does not exist yet.
    :type path: Path
    :param content: Bytes of the tile file.
    :type content: bytes
    """
    path.mkdir()
    (path / TILE_NAME).write_bytes(content)


async def _store_file(store: AssetStore, *, key: StorageKey, content: bytes) -> None:
    """Write one file at ``key``.

    :param store: Asset store to write into.
    :type store: AssetStore
    :param key: Key to store the file at.
    :type key: StorageKey
    :param content: Bytes of the file.
    :type content: bytes
    """
    async with store.writable(key) as path:
        path.write_bytes(content)


async def _store_directory(store: AssetStore, *, key: StorageKey, content: bytes) -> None:
    """Write a directory holding one tile file at ``key``.

    :param store: Asset store to write into.
    :type store: AssetStore
    :param key: Key to store the directory at.
    :type key: StorageKey
    :param content: Bytes of the tile file.
    :type content: bytes
    """
    async with store.writable(key) as path:
        _fill_directory(path, content)


async def _read_file(store: AssetStore, key: StorageKey) -> bytes:
    """Return the content of the file stored at ``key``.

    :param store: Asset store to read from.
    :type store: AssetStore
    :param key: Key the file is stored at.
    :type key: StorageKey
    :returns: Bytes of the stored file.
    :rtype: bytes
    """
    async with store.readable(key) as path:
        return path.read_bytes()


async def _list_directory(store: AssetStore, key: StorageKey) -> dict[str, bytes]:
    """Return the files of the directory stored at ``key`` by name.

    :param store: Asset store to read from.
    :type store: AssetStore
    :param key: Key the directory is stored at.
    :type key: StorageKey
    :returns: Content of every file in the directory, by file name.
    :rtype: dict[str, bytes]
    """
    async with store.readable(key) as path:
        return {child.name: child.read_bytes() for child in path.iterdir()}


async def _read_tile(store: AssetStore, key: StorageKey) -> bytes:
    """Return the content of the tile file in the directory stored at ``key``.

    :param store: Asset store to read from.
    :type store: AssetStore
    :param key: Key the directory is stored at.
    :type key: StorageKey
    :returns: Bytes of the tile file.
    :rtype: bytes
    """
    async with store.readable(key) as path:
        return (path / TILE_NAME).read_bytes()


async def _is_stored(store: AssetStore, key: StorageKey) -> bool:
    """Return whether anything is stored at ``key``.

    :param store: Asset store to look into.
    :type store: AssetStore
    :param key: Key to look up.
    :type key: StorageKey
    :returns: True when a file or directory is stored at the key.
    :rtype: bool
    """
    try:
        async with store.readable(key):
            return True
    except NotFoundError:
        return False


class StoredKeyCase(NamedTuple):
    """A key holding a file or a directory, with how to store content there and read it back.

    :ivar key: Key of the file or directory.
    :ivar fill: Function making a writer's path the file or directory with given content.
    :ivar store: Coroutine function storing content at a key through a complete write.
    :ivar read: Coroutine function reading back the content stored at a key.
    """

    key: StorageKey
    fill: Callable[[Path, bytes], None]
    store: Callable[..., Awaitable[None]]
    read: Callable[[AssetStore, StorageKey], Awaitable[bytes]]


STORED_KEY_CASES: list[StoredKeyCase] = [
    StoredKeyCase(key=FILE_KEY, fill=_fill_file, store=_store_file, read=_read_file),
    StoredKeyCase(key=DIRECTORY_KEY, fill=_fill_directory, store=_store_directory, read=_read_tile),
]
STORED_KEY_IDS: list[str] = ['file', 'directory']


async def _start_write(store: AssetStore, *, key: StorageKey, started: list[Path]) -> None:
    """Open a write at ``key`` and record the path it was given, which a refused write never gets.

    :param store: Asset store to write into.
    :type store: AssetStore
    :param key: Key to open the write at.
    :type key: StorageKey
    :param started: List receiving the path when the write opens.
    :type started: list[Path]
    """
    async with store.writable(key) as path:
        started.append(path)


async def _write_while_another_finishes(store: AssetStore, case: StoredKeyCase) -> None:
    """Write NEW_CONTENT at the case's key while another writer stores OLD_CONTENT there and finishes first.

    :param store: Asset store both writers write into.
    :type store: AssetStore
    :param case: Key, and how to write and store a file or a directory there.
    :type case: StoredKeyCase
    """
    async with store.writable(case.key) as path:
        case.fill(path, NEW_CONTENT)
        await case.store(store, key=case.key, content=OLD_CONTENT)


class TestWritable:
    """Contract of AssetStore.writable()."""

    async def test_publishes_file_only_on_exit(self, fx_asset_store: AssetStore) -> None:
        """Verify a file being written is invisible to readers until the writer finishes.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        async with fx_asset_store.writable(FILE_KEY) as path:
            path.write_bytes(NEW_CONTENT)
            expect(not await _is_stored(fx_asset_store, FILE_KEY))

        expect(await _read_file(fx_asset_store, FILE_KEY) == NEW_CONTENT)
        assert_expectations()

    async def test_publishes_directory_only_on_exit(self, fx_asset_store: AssetStore) -> None:
        """Verify a pyramid being cut is invisible to readers until the writer finishes.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        async with fx_asset_store.writable(DIRECTORY_KEY) as path:
            path.mkdir()
            (path / TILE_NAME).write_bytes(NEW_CONTENT)
            expect(not await _is_stored(fx_asset_store, DIRECTORY_KEY))

        expect(await _list_directory(fx_asset_store, DIRECTORY_KEY) == {TILE_NAME: NEW_CONTENT})
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, STORED_KEY_CASES, ids=STORED_KEY_IDS)
    async def test_refuses_stored_key_before_writing(self, fx_asset_store: AssetStore, case: StoredKeyCase) -> None:
        """Verify a stored file or pyramid is never replaced, and the writer is stopped before it starts.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        :param case: A key holding a file or a directory, with how to store content there and read it back.
        :type case: StoredKeyCase
        """
        await case.store(fx_asset_store, key=case.key, content=OLD_CONTENT)
        started: list[Path] = []

        with pytest.raises(ConflictError):
            await _start_write(fx_asset_store, key=case.key, started=started)

        expect(started == [])
        expect(await case.read(fx_asset_store, case.key) == OLD_CONTENT)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, STORED_KEY_CASES, ids=STORED_KEY_IDS)
    async def test_refuses_key_stored_while_writing(self, fx_asset_store: AssetStore, case: StoredKeyCase) -> None:
        """Verify a writer that finishes second is refused and the first writer's content stays.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        :param case: A key holding a file or a directory, with how to store content there and read it back.
        :type case: StoredKeyCase
        """
        with pytest.raises(ConflictError):
            await _write_while_another_finishes(fx_asset_store, case)

        assert await case.read(fx_asset_store, case.key) == OLD_CONTENT

    async def test_failed_write_publishes_nothing(self, fx_asset_store: AssetStore) -> None:
        """Verify a writer that fails leaves the key free.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        with pytest.raises(WriterFailedError):
            await abandon_write(fx_asset_store, key=FILE_KEY, content=NEW_CONTENT)

        assert not await _is_stored(fx_asset_store, FILE_KEY)


class TestReadable:
    """Contract of AssetStore.readable()."""

    async def test_missing_key_raises_not_found(self, fx_asset_store: AssetStore) -> None:
        """Verify reading a key nothing was written to raises NotFoundError.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        with pytest.raises(NotFoundError):
            await _read_file(fx_asset_store, FILE_KEY)


class TestDeletePrefix:
    """Contract of AssetStore.delete_prefix()."""

    async def test_removes_everything_under_prefix_only(self, fx_asset_store: AssetStore) -> None:
        """Verify files and directories under the prefix are gone and a key outside it stays.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        await _store_file(fx_asset_store, key=FILE_KEY, content=NEW_CONTENT)
        await _store_directory(fx_asset_store, key=DIRECTORY_KEY, content=NEW_CONTENT)
        await _store_file(fx_asset_store, key=SIBLING_KEY, content=NEW_CONTENT)

        await fx_asset_store.delete_prefix(PAGE_PREFIX)

        expect(not await _is_stored(fx_asset_store, FILE_KEY))
        expect(not await _is_stored(fx_asset_store, DIRECTORY_KEY))
        expect(await _is_stored(fx_asset_store, SIBLING_KEY))
        assert_expectations()

    async def test_missing_prefix_is_not_an_error(self, fx_asset_store: AssetStore) -> None:
        """Verify deleting a prefix with nothing under it succeeds, so cleanup can always run.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        await fx_asset_store.delete_prefix(PAGE_PREFIX)

        assert not await _is_stored(fx_asset_store, FILE_KEY)

    async def test_frees_keys_for_writing_again(self, fx_asset_store: AssetStore) -> None:
        """Verify deleting a prefix is the way to write its keys again, as a retried import does.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        """
        await _store_file(fx_asset_store, key=FILE_KEY, content=OLD_CONTENT)
        await fx_asset_store.delete_prefix(PAGE_PREFIX)

        await _store_file(fx_asset_store, key=FILE_KEY, content=NEW_CONTENT)

        assert await _read_file(fx_asset_store, FILE_KEY) == NEW_CONTENT

    @pytest.mark.parametrize(
        KEY_ARG,
        ['projects', 'projects/book', 'projects/book/source', 'projects/book/incoming/page.png'],
    )
    async def test_refuses_keys_of_source_store(self, fx_asset_store: AssetStore, key: str) -> None:
        """Verify no key names or holds a project's source or staged upload, so a wrong key cannot erase a book.

        :param fx_asset_store: Asset store of the storage backend under test.
        :type fx_asset_store: AssetStore
        :param key: Storage key under test.
        :type key: str
        """
        with pytest.raises(ValueError, match='reaches into the files of the source store'):
            await fx_asset_store.delete_prefix(StorageKey(key))
