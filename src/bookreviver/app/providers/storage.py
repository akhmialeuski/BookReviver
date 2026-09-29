"""Providers of the source and asset storage adapters, one class per backend selectable in the settings.

``STORAGE_PROVIDERS`` maps each ``StorageBackend`` to its provider, the same way ``PERSISTENCE_PROVIDERS`` does for
persistence, so ``build_container`` selects the backend from ``Settings.storage`` and the port contract tests run
against every registered backend.
"""

from dishka import Provider, Scope, provide

from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.app.settings import Settings, StorageBackend
from bookreviver.ports.storage import AssetStore, SourceStore


class LocalStorageProvider(Provider):
    """Builds the source and asset stores, one of each per application, over the local storage root."""

    scope = Scope.APP

    @provide
    def source_store(self, settings: Settings) -> SourceStore:
        """Build the store of uploaded sources.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: The local source store under the storage root.
        :rtype: SourceStore
        """
        return LocalSourceStore(root=settings.storage_root)

    @provide
    def asset_store(self, settings: Settings) -> AssetStore:
        """Build the store of derived page images and tile pyramids.

        :param settings: Application settings, of which ``storage_root`` is read.
        :type settings: Settings
        :returns: The local asset store under the storage root.
        :rtype: AssetStore
        """
        return LocalAssetStore(root=settings.storage_root)


STORAGE_PROVIDERS: dict[StorageBackend, type[Provider]] = {
    StorageBackend.LOCAL: LocalStorageProvider,
}
