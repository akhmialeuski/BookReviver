"""Uploads as the API receives them, and writers that give up half-way, for the storage tests."""

import io
from typing import TYPE_CHECKING

from fastapi import UploadFile

if TYPE_CHECKING:
    from bookreviver.domain.ids import StorageKey
    from bookreviver.ports.storage import AssetStore

# More than one read chunk of the local store, so staging has to stream
LARGE_CONTENT: bytes = bytes(range(256)) * 10_240


class WriterFailedError(Exception):
    """Raised by a test writer to abandon a write half-way."""


def upload(name: str | None, content: bytes = b'page') -> UploadFile:
    """Return an in-memory upload named as a browser would send it."""
    return UploadFile(file=io.BytesIO(content), filename=name)


async def abandon_write(store: AssetStore, key: StorageKey, content: bytes) -> None:
    """Start writing ``content`` at ``key`` and fail before the write completes.

    :raises WriterFailedError: Always, from inside the write.
    """
    async with store.writable(key) as path:
        path.write_bytes(content)
        raise WriterFailedError
