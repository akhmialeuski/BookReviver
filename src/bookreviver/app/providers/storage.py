"""Providers of the storage adapter of the directories of the books, one class per backend selectable in the settings.

``STORAGE_PROVIDERS`` maps each ``StorageBackend`` to its provider, so ``build_container`` selects the backend from
``Settings.storage`` and the port contract tests run against every registered backend.
"""

from dishka import Provider, Scope, provide

from bookreviver.adapters.storage import LocalBookDirectories
from bookreviver.app.settings import Settings, StorageBackend
from bookreviver.ports.storage import BookDirectories


class LocalStorageProvider(Provider):
    """Builds the directories of the books, one per application, over the storage root."""

    scope = Scope.APP

    @provide
    def book_directories(self, settings: Settings) -> BookDirectories:
        """Build the directories of the books, which make, unpack, walk and open the directory of each book.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: The local directories of the books under the storage root.
        :rtype: BookDirectories
        """
        return LocalBookDirectories(root=settings.storage_root)


STORAGE_PROVIDERS: dict[StorageBackend, type[Provider]] = {
    StorageBackend.LOCAL: LocalStorageProvider,
}
