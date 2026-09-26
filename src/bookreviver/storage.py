"""On-disk layout of project files: the uploaded source and the derived render cache."""

import shutil
from typing import TYPE_CHECKING

import anyio.to_thread
from attrs import frozen

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_DIR_NAME: str = 'source'
CACHE_DIR_NAME: str = 'cache'


@frozen(kw_only=True)
class ProjectStorage:
    """Resolve and manage the directory tree ``<root>/<project id>/{source,cache}``."""

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

    async def reset_source(self, project_id: int) -> Path:
        """Remove the previous source and its cache, then return an empty source directory."""
        await self._remove_tree(self.source_dir(project_id))
        await self._remove_tree(self.cache_dir(project_id))
        source_dir = self.source_dir(project_id)
        source_dir.mkdir(parents=True)
        return source_dir

    async def delete_project(self, project_id: int) -> None:
        """Remove every file of the project."""
        await self._remove_tree(self.project_dir(project_id))

    @staticmethod
    async def _remove_tree(path: Path) -> None:
        """Delete a directory tree off the event loop; a missing tree is not an error."""
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(path, ignore_errors=True))
