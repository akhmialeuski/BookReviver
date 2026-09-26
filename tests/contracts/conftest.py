"""Fixtures giving the persistence contract tests a unit of work per request scope, for each backend."""

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.app.providers.core import CoreProvider
from bookreviver.app.providers.database import DatabaseProvider
from bookreviver.app.providers.persistence import PERSISTENCE_PROVIDERS
from bookreviver.app.settings import Settings
from bookreviver.ports.persistence import UnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

type UnitOfWorkFactory = Callable[[], Awaitable[UnitOfWork]]


@pytest.fixture(params=list(PERSISTENCE_PROVIDERS), ids=str)
async def fx_uow_factory(request: pytest.FixtureRequest, fx_settings: Settings) -> AsyncIterator[UnitOfWorkFactory]:
    """Yield a function opening a new unit of work, built by the application's own provider of one backend."""
    container = make_async_container(
        CoreProvider(),
        DatabaseProvider(),
        PERSISTENCE_PROVIDERS[request.param](),
        context={Settings: fx_settings},
    )
    async with AsyncExitStack() as scopes:

        async def open_unit_of_work() -> UnitOfWork:
            scope = await scopes.enter_async_context(container())
            return await scope.get(UnitOfWork)

        yield open_unit_of_work
    await container.close()
