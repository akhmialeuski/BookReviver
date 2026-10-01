"""The routers of every feature, in the order the application includes them, and where they are mounted."""

from bookreviver.api.routers import (
    accounts,
    catalogue,
    edits,
    iiif,
    imports,
    jobs,
    pages,
    processing,
    projects,
    sources,
)

ROUTERS = (
    accounts.router,
    projects.router,
    sources.router,
    pages.router,
    processing.router,
    edits.router,
    catalogue.router,
    imports.router,
    jobs.router,
    iiif.router,
)
# Every endpoint lives below this prefix
API_PREFIX: str = '/api/v1'
# The path the IIIF routes are served from, with which the import cuts the pyramids' ``info.json`` ids
IIIF_ROOT: str = f'{API_PREFIX}{iiif.IIIF_PREFIX}'
