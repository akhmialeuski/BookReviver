"""Assembly of the dependency container from the feature providers and the selected adapters."""

from typing import TYPE_CHECKING

from dishka import Provider, make_async_container

from bookreviver.app.providers.accounts import AccountsProvider
from bookreviver.app.providers.core import CoreProvider
from bookreviver.app.providers.database import DatabaseProvider
from bookreviver.app.providers.imaging import ImagingProvider
from bookreviver.app.providers.imports import ImportsProvider
from bookreviver.app.providers.persistence import PERSISTENCE_PROVIDERS
from bookreviver.app.providers.projects import ProjectsProvider
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import AsyncContainer


def build_container(settings: Settings, extra_providers: Sequence[Provider] = ()) -> AsyncContainer:
    """Build the container with the adapters the settings select; ``extra_providers`` come last and override."""
    return make_async_container(
        CoreProvider(),
        DatabaseProvider(),
        PERSISTENCE_PROVIDERS[settings.persistence](),
        STORAGE_PROVIDERS[settings.storage](),
        ImagingProvider(),
        AccountsProvider(),
        ImportsProvider(),
        ProjectsProvider(),
        *extra_providers,
        context={Settings: settings},
    )
