"""Domain events published while use cases run, delivered to the browser as server-sent events."""

from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job, Page
    from bookreviver.domain.ids import ProjectId


@frozen(kw_only=True)
class DomainEvent:
    """Something that happened within one project."""

    project_id: ProjectId


@frozen(kw_only=True)
class JobChanged(DomainEvent):
    """A job changed state or progress."""

    job: Job


@frozen(kw_only=True)
class PageReady(DomainEvent):
    """The derived images of a page can be shown."""

    page: Page


@frozen(kw_only=True)
class ProjectChanged(DomainEvent):
    """The description or the source of a project changed."""
