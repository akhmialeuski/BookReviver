"""On-disk layout of project files: the uploaded source and the derived render cache."""

import shutil
from typing import TYPE_CHECKING

import anyio.to_thread
from attrs import frozen

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_DIR_NAME: str = 'source'
INCOMING_DIR_NAME: str = 'incoming'
CACHE_DIR_NAME: str = 'cache'


@frozen(kw_only=True)
class ProjectStorage:
    """Resolve and manage the directory tree ``<root>/<project id>/{source,incoming,cache}``.

    A new source is written to ``incoming`` and replaces ``source`` only once it proved readable, so a failed
    replacement never loses the book that was already imported.
    """

    root: Path

    def project_dir(self, project_id: int) -> Path:
        """Return the directory that holds every file of one project."""
        return self.root / str(project_id)

    def source_dir(self, project_id: int) -> Path:
        """Return the directory with the uploaded book exactly as received."""
        return self.project_dir(project_id) / SOURCE_DIR_NAME

    def cache_dir(self, project_id: int) -> Path:
        """Return the directory with derived files that can be regenerated at any time."""
        return self.project_dir(project_id) / CACHE_DIR_NAME

    def incoming_dir(self, project_id: int) -> Path:
        """Return the directory where a new source is written before it replaces the current one."""
        return self.project_dir(project_id) / INCOMING_DIR_NAME

    async def start_incoming(self, project_id: int) -> Path:
        """Return an empty incoming directory, dropping whatever an interrupted upload left there."""
        incoming_dir = self.incoming_dir(project_id)
        await self._remove_tree(incoming_dir)
        incoming_dir.mkdir(parents=True)
        return incoming_dir

    async def promote_incoming(self, project_id: int) -> Path:
        """Replace the source with the incoming files, drop the cache rendered from the old one, return the source."""
        source_dir = self.source_dir(project_id)
        await self._remove_tree(source_dir)
        await self._remove_tree(self.cache_dir(project_id))
        # A rename within one directory is atomic, so the source is never half replaced
        self.incoming_dir(project_id).rename(source_dir)
        return source_dir

    async def discard_incoming(self, project_id: int) -> None:
        """Remove a rejected upload, leaving the current source untouched."""
        await self._remove_tree(self.incoming_dir(project_id))

    async def delete_project(self, project_id: int) -> None:
        """Remove every file of the project."""
        await self._remove_tree(self.project_dir(project_id))

    @staticmethod
    async def _remove_tree(path: Path) -> None:
        """Delete a directory tree off the event loop; a missing tree is not an error."""
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(path, ignore_errors=True))
