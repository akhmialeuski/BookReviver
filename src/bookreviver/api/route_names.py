"""Names of routes that other routes link to, so URLs are built with ``request.url_for`` and never by hand."""

import enum


class RouteName(enum.StrEnum):
    """Route names shared between routers."""

    IIIF_FILE = 'iiif-file'
    PROJECT = 'project'
    PROJECT_EVENTS = 'project-events'
