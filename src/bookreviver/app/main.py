"""Application factory: wires the container, error handling, pagination, security and routers."""

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from dishka.integrations.fastapi import setup_dishka
from fastapi import APIRouter, FastAPI
from fastapi_pagination import add_pagination
from fastapi_problem.handler import add_exception_handler

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.api.auth import signed_in_user
from bookreviver.api.problems import problem_handler
from bookreviver.api.routing import API_PREFIX, ROUTERS
from bookreviver.app.container import build_container
from bookreviver.app.providers.accounts import account_routes
from bookreviver.app.security import install_security, sign_in_throttle
from bookreviver.app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from typing import Any

    from dishka import Provider
    from httpx_oauth.oauth2 import BaseOAuth2

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    extra_providers: Sequence[Provider] = (),
    social_clients: Sequence[BaseOAuth2[Any]] | None = None,
) -> FastAPI:
    """Build the API application; the container is closed when the application shuts down.

    :param settings: Configuration, read from the environment when omitted.
    :type settings: Settings | None
    :param extra_providers: Providers added last, overriding earlier ones; tests use them to inject fakes.
    :type extra_providers: Sequence[Provider]
    :param social_clients: Social sign-in clients to offer instead of those the credentials in ``settings`` enable; the
                           tests and the end-to-end server use it to offer a fake provider.
    :type social_clients: Sequence[BaseOAuth2[Any]] | None
    :returns: The application with its routers, exception handler, pagination, security and container installed, and
              the built frontend when ``settings.frontend_dir`` exists.
    :rtype: FastAPI
    """
    resolved = settings or Settings()
    container = build_container(resolved, extra_providers)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Open the database, run the application and close the container when it shuts down.

        The container opens the database on first use, and opening it checks the schema revision, so the database is
        opened here: a database the migrations were not applied to stops the start instead of failing a request.

        :param _app: The application, required by FastAPI's lifespan signature and unused.
        :type _app: FastAPI
        :returns: Iterator yielding once while the application runs.
        :rtype: AsyncIterator[None]
        :raises RuntimeError: When the database is not at the head revision of the migrations.
        """
        try:
            await container.get(SqlDatabase)
            yield
        finally:
            await container.close()

    app = FastAPI(title='BookReviver', lifespan=lifespan)
    add_exception_handler(app, problem_handler(logger))
    add_pagination(app)
    accounts = account_routes(resolved, sign_in_throttle(), social_clients)
    api = APIRouter(prefix=API_PREFIX)
    for router in ROUTERS:
        api.include_router(router)
    api.include_router(accounts.router())
    app.include_router(api)
    app.dependency_overrides[signed_in_user] = accounts.current_user
    install_security(app, resolved)
    setup_dishka(container, app)
    # FastAPI checks its own routes first and the frontend only for what no route matched, so it is added last and
    # an address of the API is never answered with a page. Without a build there is nothing to serve, which is the
    # normal state of the backend on its own and of a development machine that runs the Vite server instead.
    if resolved.frontend_dir.is_dir():
        app.frontend('/', directory=resolved.frontend_dir)
    else:
        logger.info('No built frontend in %s, so only the API is served.', resolved.frontend_dir)
    return app
