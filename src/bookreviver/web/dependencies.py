"""FastAPI dependencies giving handlers typed access to what the lifespan put on ``app.state``."""

from typing import TYPE_CHECKING, Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.config import Settings
from bookreviver.storage import ProjectStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import async_sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one session per request from the factory the application stored at startup."""
    session_factory = cast('async_sessionmaker[AsyncSession]', request.app.state.session_factory)
    # try/finally rather than `async with`: FastAPI drives this generator, so ASYNC119 applies
    session = session_factory()
    try:
        yield session
    finally:
        await session.close()


def get_storage(request: Request) -> ProjectStorage:
    """Return the project file storage created at startup."""
    return cast('ProjectStorage', request.app.state.storage)


def get_settings(request: Request) -> Settings:
    """Return the settings the application was created with."""
    return cast('Settings', request.app.state.settings)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[ProjectStorage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
