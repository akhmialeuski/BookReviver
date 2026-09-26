"""Assembly of the dependency container from the feature providers and the selected adapters."""

from typing import TYPE_CHECKING

from dishka import make_async_container

from bookreviver.app.providers.accounts import AccountsProvider
from bookreviver.app.providers.core import CoreProvider
from bookreviver.app.providers.imports import ImportsProvider
from bookreviver.app.providers.persistence import PERSISTENCE_PROVIDERS
from bookreviver.app.providers.projects import ProjectsProvider
from bookreviver.app.settings import Settings

if TYPE_CHECKING:
    from dishka import AsyncContainer


def build_container(settings: Settings) -> AsyncContainer:
    """Build the container with the adapters the settings select."""
    return make_async_container(
        CoreProvider(),
        PERSISTENCE_PROVIDERS[settings.persistence](),
        AccountsProvider(),
        ImportsProvider(),
        ProjectsProvider(),
        context={Settings: settings},
    )
