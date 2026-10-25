"""Providers of the storage adapter of the directories of the books, one class per backend selectable in the settings.

``STORAGE_PROVIDERS`` maps each ``StorageBackend`` to its provider, so ``build_container`` selects the backend from
``Settings.storage`` and the port contract tests run against every registered backend. A provider provides the opener
of its backend, the function from the directory name of a book to its ``BookDirectory``, which ``prepare_start`` and the
``bookreviver-migrate`` command register with ``BookDirectory.register``.
"""

from collections.abc import Callable

from dishka import Provider, Scope, provide

from bookreviver.adapters.storage import LocalBookDirectory
from bookreviver.app.settings import Settings, StorageBackend
from bookreviver.domain.ids import BookDirectoryName
from bookreviver.ports.storage import BookDirectory


class LocalStorageProvider(Provider):
    """Provides the opener of the local directories of the books, over the storage root."""

    scope = Scope.APP

    @provide
    def book_directory(self, settings: Settings) -> Callable[[BookDirectoryName], BookDirectory]:
        """Build the function that opens the local directory of a book under the storage root.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: Function from the directory name of a book to its local directory.
        :rtype: Callable[[BookDirectoryName], BookDirectory]
        """
        root = settings.storage_root
        return lambda dir_name: LocalBookDirectory(dir_name, root=root)


STORAGE_PROVIDERS: dict[StorageBackend, type[Provider]] = {
    StorageBackend.LOCAL: LocalStorageProvider,
}
