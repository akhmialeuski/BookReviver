"""Domain events published while use cases run, delivered to the browser as server-sent events.

Each event names what changed in the book, so the browser reads again only the part it shows: a job, a source with its
scans, the images of one scan, the order of the pages, one page version, or the description of the book.
"""

from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job, PageVersion, Scan, Source
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
class SourceImported(DomainEvent):
    """A source and its scans were committed to the project.

    :ivar source: The imported source.
    """

    source: Source


@frozen(kw_only=True)
class ScanReady(DomainEvent):
    """The derived files of a scan can be shown.

    :ivar scan: The scan whose renditions are ready.
    """

    scan: Scan


@frozen(kw_only=True)
class PagesChanged(DomainEvent):
    """Pages of the book were added, removed or moved, or their labels or kinds changed."""


@frozen(kw_only=True)
class PageVersionReady(DomainEvent):
    """A page version has its result.

    :ivar version: The ready page version.
    """

    version: PageVersion


@frozen(kw_only=True)
class ProjectChanged(DomainEvent):
    """The description of the book changed."""
