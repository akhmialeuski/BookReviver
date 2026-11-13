"""The collaborators every processing use case is built from, and the starting of the jobs that do the heavy work.

A use case of processing writes recipes and stage records, follows jobs and starts new ones. ``ProcessingParts`` gathers
the objects that do these on the book a use case is given, so ``ProcessingService`` and ``ProcessingJobs`` take one
value for them and are built the same way.

A job is what was asked for: a step or a stage, the pages it goes over, and its parameters, as the value of its kind
(``Job.work``). It is put on the job queue of its book, which runs one job of the book at a time in the order they were
put: first in, first out. A run of the geometry followed by a run of the cleanup is two jobs, and the second starts when
the first has ended, so it reads what the first made. Nothing is refused because the book is busy. A job that follows
from the one that is running, such as the split of the pages an import made, is put next up, ahead of the queued jobs,
so that what the user asked for meanwhile works on what the follow-up made.

A preview is the one job that is dropped for another. It is a look at one page that changes nothing that is current,
so a run, a measure of the book, a collection, a clear of a step on a page and a choice of a version the user asks for
cancel the previews of the book that are queued or running, and a new preview takes the place of the one before it. A
tile cutting cancels nothing. The collection that every run queues when it ends is left out while a collection of the
book is queued already, since one is enough.

The job is put with no ``await`` before it, so no other request can put a job in between. A cancelled preview that is
running keeps its place until its current task has returned, and the job put behind it starts only then.
"""

from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import field, frozen, validators

from bookreviver.domain.errors import JobFinishedError, NotFoundError
from bookreviver.domain.ids import JobId
from bookreviver.domain.jobs import Job, JobKind, JobState, TileCut, VersionCollection
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipes import RecipeBook
from bookreviver.services.stage_records import StageRecords

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from bookreviver.core.book import Book
    from bookreviver.core.plugins import ProcessorCatalog
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.domain.jobs import JobWork
    from bookreviver.services.recipes import DefaultRecipes


@frozen(kw_only=True)
class ProcessingConfig:
    """The bounds of processing, from the settings.

    :ivar preview_retention: How long a preview is kept before a collection may delete it.
    :ivar preview_long_side_px: Longer side of a preview in pixels.
    :ivar parallel_pages: Largest number of pages a run processes at the same time.
    """

    preview_retention: timedelta
    preview_long_side_px: int
    parallel_pages: int = field(default=1, validator=validators.ge(1))


class JobStarter:
    """Puts the jobs of a book on its queue, which runs them one at a time in the order they were put."""

    def __init__(self, *, config: ProcessingConfig) -> None:
        """Start jobs through the queue of a book.

        :param config: The retention period of a preview.
        :type config: ProcessingConfig
        """
        self._config = config

    async def enqueue(self, book: Book, kind: JobKind, work: JobWork | None = None, *, next_up: bool = False) -> Job:
        """Put a job on the queue of the book, behind the jobs the book has, and drop the previews it replaces.

        The job starts when every job put before it has ended, so two jobs of a book never overlap and a later one
        reads what an earlier one made. A run, a measure of the book, a clear of a step, a choice of a version and a
        new preview cancel the previews of the book, queued or running, the way an account holder cancels a job, after
        the new job is put. A preview that was
        still queued never runs. One that is running stops at the boundary of its current task, and the new job starts
        after that.

        :param book: The open book.
        :type book: Book
        :param kind: What the job does.
        :type kind: JobKind
        :param work: What the job was asked to do, as the value of its kind, or None for a job that takes nothing.
        :type work: JobWork | None
        :param next_up: Whether the job follows from the one that is running, so it runs before the jobs that are
                        queued already.
        :type next_up: bool
        :returns: The job as the queue holds it, queued.
        :rtype: Job
        """
        previews = self._active(book)[0] if kind in JobKind.replacing_previews() else []
        job = book.jobs.put(self._new_job(kind, work), next_up=next_up)
        await self._cancel(book, previews)
        return job

    def collection(self) -> VersionCollection:
        """Say what a collection started now deletes: the versions nothing needs, and the previews old enough.

        :returns: The collection, whose moment is the retention of a preview before now.
        :rtype: VersionCollection
        """
        return VersionCollection(previews_older_than=datetime.now(UTC) - self._config.preview_retention)

    async def enqueue_collection(self, book: Book) -> Job:
        """Queue the collection the user asked for, unless one is queued already, and drop the previews of the book.

        :param book: The open book.
        :type book: Book
        :returns: The collection job, new or the one that was queued already.
        :rtype: Job
        """
        previews, _ = self._active(book)
        job = self.queue_collection(book)
        await self._cancel(book, previews)
        return job

    def queue_collection(self, book: Book) -> Job:
        """Queue a collection of the book's old versions behind the jobs the book has, unless one is queued already.

        A collection that is queued deletes what is not needed when it starts, so a second one behind it would find
        nothing left. One that is running has chosen what it deletes already, so a new one is queued behind it.

        :param book: The open book.
        :type book: Book
        :returns: The collection job, new or the one that was queued already.
        :rtype: Job
        """
        _, others = self._active(book)
        for job in others:
            if job.kind is JobKind.COLLECT_VERSIONS and job.state is JobState.QUEUED:
                return job
        return book.jobs.put(self._new_job(JobKind.COLLECT_VERSIONS, self.collection()))

    async def enqueue_tiles(self, book: Book, version_ids: Sequence[PageVersionId]) -> Job | None:
        """Queue the cutting of the pyramids of versions the running job just made the current ones of their stages.

        The change that made them current is committed already. The viewer asks for the pyramid of a current version,
        and only the last step of a run cuts one, so the version of a step in the middle of a recipe has none until it
        is cut here. The cutting follows from the job that made the change, a clear of a step or a choice of a version,
        so it is put next up, ahead of the jobs that are queued already.

        :param book: The open book.
        :type book: Book
        :param version_ids: Versions to cut.
        :type version_ids: Sequence[PageVersionId]
        :returns: The tile cutting job, or None when there is no version to cut.
        :rtype: Job | None
        """
        if not version_ids:
            return None
        return book.jobs.put(self._new_job(JobKind.CUT_TILES, TileCut(version_ids=tuple(version_ids))), next_up=True)

    def _active(self, book: Book) -> tuple[list[Job], list[Job]]:
        """Read the jobs of the book that process its versions and are queued or running.

        :param book: The open book.
        :type book: Book
        :returns: The previews, and the other jobs, each newest first.
        :rtype: tuple[list[Job], list[Job]]
        """
        active = [job for job in book.jobs.active() if job.kind in JobKind.processing()]
        previews = [job for job in active if job.kind is JobKind.PREVIEW_STEP]
        return previews, [job for job in active if job not in previews]

    async def _cancel(self, book: Book, jobs: Sequence[Job]) -> None:
        """Cancel jobs the way an account holder cancels them, leaving out one that has ended meanwhile.

        :param book: The open book whose queue holds the jobs.
        :type book: Book
        :param jobs: The jobs to cancel.
        :type jobs: Sequence[Job]
        """
        for job in jobs:
            with suppress(JobFinishedError, NotFoundError):
                await book.jobs.cancel(job.id)

    @staticmethod
    def _new_job(kind: JobKind, work: JobWork | None) -> Job:
        """Build a queued job.

        :param kind: What the job does.
        :type kind: JobKind
        :param work: What the job was asked to do, or None for a job that takes nothing.
        :type work: JobWork | None
        :returns: The job, queued as of now.
        :rtype: Job
        """
        return Job(id=JobId(uuid4()), kind=kind, work=work, created_at=datetime.now(UTC))


@frozen(kw_only=True)
class ProcessingParts:
    """The objects a processing use case works through, built for one request or job.

    :ivar recipes: The recipes of the book and the check of their steps.
    :ivar records: Writer of the stage records.
    :ivar starter: Putter of new jobs on the queue of a book.
    """

    recipes: RecipeBook
    records: StageRecords
    starter: JobStarter

    @classmethod
    def build(
        cls,
        processors: ProcessorCatalog,
        defaults: DefaultRecipes,
        config: ProcessingConfig,
    ) -> ProcessingParts:
        """Build the parts of one request or job.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param defaults: The recipes a stage starts with.
        :type defaults: DefaultRecipes
        :param config: The retention period and the size of a preview.
        :type config: ProcessingConfig
        :returns: The parts.
        :rtype: ProcessingParts
        """
        return cls(
            recipes=RecipeBook(processors=processors, defaults=defaults, order=RecipeOrder(processors)),
            records=StageRecords(),
            starter=JobStarter(config=config),
        )
