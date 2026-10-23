"""Domain events published while use cases run, delivered to the browser as server-sent events.

A committed change of a book is one ``BookChanged``, which the unit of work publishes with the parts of the book the
change wrote, so the browser reads again only those parts. The jobs live in the memory of the process and raise no
version of the book, so a job publishes ``JobChanged`` with the whole job, and a version a page gains is announced by
``PageVersionReady`` with its identifier, which a preview waits for.
"""

from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job, PageVersion
    from bookreviver.domain.enums import BookResource
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
class PageVersionReady(DomainEvent):
    """A page version has its result.

    :ivar version: The ready page version.
    """

    version: PageVersion


@frozen(kw_only=True)
class BookChanged(DomainEvent):
    """A change of the book committed, which raised its version by one.

    :ivar version: The version of the book after the change, one more than before it.
    :ivar resources: The parts of the book the change wrote, which are the reads to repeat.
    """

    version: int
    resources: frozenset[BookResource]
