"""Domain events published while use cases run, delivered to the browser as server-sent events."""

from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job, Page
    from bookreviver.domain.ids import ProjectId


@frozen(kw_only=True)
class DomainEvent:
    """Something that happened within one project.

    :ivar project_id: Project the event belongs to, whose subscribers receive it.
    """

    project_id: ProjectId


@frozen(kw_only=True)
class JobChanged(DomainEvent):
    """A job changed state or progress.

    :ivar job: The job in its new state.
    """

    job: Job


@frozen(kw_only=True)
class PageReady(DomainEvent):
    """The derived images of a page can be shown.

    :ivar page: The page whose assets are ready.
    """

    page: Page


@frozen(kw_only=True)
class ProjectChanged(DomainEvent):
    """The description or the source of a project changed."""
