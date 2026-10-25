"""Providers of the storage adapter of the directories of the books, one class per backend selectable in the settings.

``STORAGE_PROVIDERS`` maps each ``StorageBackend`` to its provider, so ``build_container`` selects the backend from
``Settings.storage`` and the port contract tests run against every registered backend. A provider provides the
``BookDirectory`` class and registers the way it opens the directory of a book, so getting it, which ``prepare_start``
does before the first request, is what makes every book opened afterwards find its directory.
"""

from collections.abc import Iterator
from functools import partial

from dishka import Provider, Scope, provide

from bookreviver.adapters.storage import LocalBookDirectory
from bookreviver.app.settings import Settings, StorageBackend
from bookreviver.ports.storage import BookDirectory


class LocalStorageProvider(Provider):
    """Registers the local directories of the books, one registration per application, over the storage root."""

    scope = Scope.APP

    @provide
    def book_directory(self, settings: Settings) -> Iterator[type[BookDirectory]]:
        """Register the local directory under the storage root as the way a book opens its directory.

        The registration is given back when the application closes the container, so the storage of another
        application stays registered.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: Iterator yielding the ``BookDirectory`` class, which opens the directory of each book by its name.
        :rtype: Iterator[type[BookDirectory]]
        """
        previous = BookDirectory.register(partial(LocalBookDirectory, root=settings.storage_root))
        try:
            yield BookDirectory
        finally:
            BookDirectory.register(previous)


STORAGE_PROVIDERS: dict[StorageBackend, type[Provider]] = {
    StorageBackend.LOCAL: LocalStorageProvider,
}
