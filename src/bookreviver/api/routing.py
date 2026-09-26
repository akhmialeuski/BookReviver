"""The routers of every feature, in the order the application includes them."""

from bookreviver.api.routers import accounts, iiif, imports, jobs, pages, projects

ROUTERS = (accounts.router, projects.router, pages.router, imports.router, jobs.router, iiif.router)
