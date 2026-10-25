"""The routers of every feature, in the order the application includes them, and where they are mounted."""

from fastapi import APIRouter, Depends, FastAPI

from bookreviver.api.access import access
from bookreviver.api.routers import (
    books,
    catalogue,
    edits,
    iiif,
    imports,
    jobs,
    page_history,
    page_settings,
    pages,
    pagination,
    places,
    processing,
    profiles,
    result_marks,
    sources,
    stages,
)

# Every one of these is mounted under the guard that refuses a request without a session
GUARDED_ROUTERS = (
    books.router,
    sources.router,
    pages.router,
    pagination.router,
    stages.router,
    places.router,
    processing.router,
    profiles.router,
    edits.router,
    page_settings.router,
    page_history.router,
    result_marks.router,
    catalogue.router,
    imports.router,
    jobs.router,
)
# Served to anyone who holds the address
PUBLIC_ROUTERS = (iiif.router,)
# Every endpoint lives below this prefix
API_PREFIX: str = '/api/v1'


def mount_api(app: FastAPI, account_routes: APIRouter) -> None:
    """Mount every router of the API below the prefix, the guarded ones behind the check of the session.

    The guard is a dependency of the router they are all mounted on, so a route that declares nothing is still refused
    without a session. The routes of sign-in and of the signed-in account, which the application builds from its
    settings, are mounted beside the public routers, outside the guard.

    :param app: The application to mount the routers on.
    :type app: FastAPI
    :param account_routes: The routes of sign-in, registration and the signed-in account.
    :type account_routes: APIRouter
    """
    guarded = APIRouter(prefix=API_PREFIX, dependencies=[Depends(access)])
    for router in GUARDED_ROUTERS:
        guarded.include_router(router)
    public = APIRouter(prefix=API_PREFIX)
    for router in PUBLIC_ROUTERS:
        public.include_router(router)
    public.include_router(account_routes)
    app.include_router(guarded)
    app.include_router(public)
