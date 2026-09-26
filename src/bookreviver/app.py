"""Application factory wiring settings, database, storage and routers."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from bookreviver.config import Settings
from bookreviver.db import database
from bookreviver.storage import ProjectStorage
from bookreviver.web import pages, projects
from bookreviver.web.templating import STATIC_DIR

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application; state that needs I/O is created in the lifespan."""
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings.projects_dir.mkdir(parents=True, exist_ok=True)
        app.state.settings = resolved_settings
        app.state.storage = ProjectStorage(root=resolved_settings.projects_dir)
        async with database(database_url=resolved_settings.database_url) as session_factory:
            app.state.session_factory = session_factory
            yield

    app = FastAPI(title='BookReviver', lifespan=lifespan)
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
    # The Import stage has its own handler, so it must be matched before the generic stage route
    app.include_router(pages.router)
    app.include_router(projects.router)
    return app
