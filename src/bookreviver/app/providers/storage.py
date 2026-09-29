"""Providers of the source and asset storage adapters, one class per backend selectable in the settings."""

from dishka import Provider, Scope, provide

from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.app.settings import Settings, StorageBackend
from bookreviver.ports.storage import AssetStore, SourceStore


class LocalStorageProvider(Provider):
    """Builds the source and asset stores, one of each per application, over the local storage root."""

    scope = Scope.APP

    @provide
    def source_store(self, settings: Settings) -> SourceStore:
        """Build the store of uploaded sources."""
        return LocalSourceStore(root=settings.storage_root)

    @provide
    def asset_store(self, settings: Settings) -> AssetStore:
        """Build the store of derived page images and tile pyramids."""
        return LocalAssetStore(root=settings.storage_root)


STORAGE_PROVIDERS: dict[StorageBackend, type[Provider]] = {
    StorageBackend.LOCAL: LocalStorageProvider,
}
