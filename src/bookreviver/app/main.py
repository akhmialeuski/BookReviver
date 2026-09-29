"""Application factory: wires the container, error handling, pagination, security and routers."""

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dishka.integrations.fastapi import setup_dishka
from fastapi import APIRouter, FastAPI
from fastapi_pagination import add_pagination
from fastapi_problem.handler import add_exception_handler

from bookreviver.api.problems import problem_handler
from bookreviver.api.routing import ROUTERS
from bookreviver.app.container import build_container
from bookreviver.app.security import install_security
from bookreviver.app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from dishka import Provider

API_PREFIX: str = '/api/v1'
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, extra_providers: Sequence[Provider] = ()) -> FastAPI:
    """Build the API application; the container is closed when the application shuts down.

    :param settings: Configuration, read from the environment when omitted.
    :type settings: Settings | None
    :param extra_providers: Providers added last, overriding earlier ones; tests use them to inject fakes.
    :type extra_providers: Sequence[Provider]
    :returns: The application with its routers, exception handler, pagination, security and container installed.
    :rtype: FastAPI
    """
    resolved = settings or Settings()
    container = build_container(resolved, extra_providers)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Run the application and close the container when it shuts down.

        :param _app: The application, required by FastAPI's lifespan signature and unused.
        :type _app: FastAPI
        :returns: Iterator yielding once while the application runs.
        :rtype: AsyncIterator[None]
        """
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title='BookReviver', lifespan=lifespan)
    add_exception_handler(app, problem_handler(logger))
    add_pagination(app)
    install_security(app, resolved)
    api = APIRouter(prefix=API_PREFIX)
    for router in ROUTERS:
        api.include_router(router)
    app.include_router(api)
    setup_dishka(container, app)
    return app
