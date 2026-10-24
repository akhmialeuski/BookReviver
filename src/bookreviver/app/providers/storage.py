"""Providers of the source and asset storage adapters, one class per backend selectable in the settings.

``STORAGE_PROVIDERS`` maps each ``StorageBackend`` to its provider, so ``build_container`` selects the backend from
``Settings.storage`` and the port contract tests run against every registered backend.
"""

from dishka import Provider, Scope, provide

from bookreviver.adapters.storage import LocalBookArchive, LocalBookFileStores
from bookreviver.app.settings import Settings, StorageBackend
from bookreviver.ports.storage import BookArchive, BookFileStores


class LocalStorageProvider(Provider):
    """Builds the opener of the stores of each book and the archive, one of each per application, over the root."""

    scope = Scope.APP

    @provide
    def book_file_stores(self, settings: Settings) -> BookFileStores:
        """Build the opener of the source store and the asset store of each book.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: The local stores of the books under the storage root.
        :rtype: BookFileStores
        """
        return LocalBookFileStores(root=settings.storage_root)

    @provide
    def book_archive(self, settings: Settings) -> BookArchive:
        """Build the archive of whole books, which packs the directory of a book and unpacks one as a new book.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: The local book archive under the storage root.
        :rtype: BookArchive
        """
        return LocalBookArchive(root=settings.storage_root)


STORAGE_PROVIDERS: dict[StorageBackend, type[Provider]] = {
    StorageBackend.LOCAL: LocalStorageProvider,
}
