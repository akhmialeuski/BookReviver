"""The collaborators every processing use case is built from, and the starting of the jobs that do the heavy work.

A use case of processing writes recipes and stage records, follows jobs and starts new ones. ``ProcessingParts`` gathers
the objects that do these over one catalogue database, so ``ProcessingService`` and ``ProcessingJobs`` take one value
for them and are built the same way.

A job is put on the job queue of its book, which queues it behind the jobs the book has already and runs one job of the
book at a time. A book processes one thing at a time. The user asks for a run, a preview or a measure of the book, of
which the book has one, and the application queues a tile cutting and a collection of old versions for itself, of
which it has one too. A request for a second of the first kind is refused, except that a run or a measure takes the
book from a preview by cancelling it, since a preview is a look that changes nothing. A request that comes while a
job of the other kind is active is queued behind it, so the tile cutting of the viewer never refuses a run and a run
never waits on a collection with a 409. The collection that every run queues is put behind the run when it ends, and is
left out while a housekeeping job is active.

The rules are checked against the jobs of the book and the job is put with no ``await`` in between, so no other request
can put a job between the check and the put. A cancelled preview that is running keeps its place until its current task
has returned, and the job put behind it starts only then.
"""

from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import frozen

from bookreviver.domain.entities import Job
from bookreviver.domain.enums import JobKind
from bookreviver.domain.errors import ConflictError, JobFinishedError, NotFoundError
from bookreviver.domain.ids import JobId
from bookreviver.domain.values import TileCut, VersionCollection
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipes import RecipeBook
from bookreviver.services.stage_records import StageRecords

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from bookreviver.domain.ids import PageVersionId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.services.book import Book
    from bookreviver.services.recipes import DefaultRecipes

BOOK_BUSY: str = (
    'The book is busy with another run, preview, measure of the book, tile cutting or collection of old versions. '
    'Wait for it to end, or cancel it.'
)


@frozen(kw_only=True)
class ProcessingConfig:
    """The bounds of processing, from the settings.

    :ivar preview_retention: How long a preview is kept before a collection may delete it.
    :ivar preview_long_side_px: Longer side of a preview in pixels.
    """

    preview_retention: timedelta
    preview_long_side_px: int


class JobStarter:
    """Puts the jobs of a book on its queue, under the rules of what a book may process at once."""

    def __init__(self, *, config: ProcessingConfig) -> None:
        """Start jobs through the queue of a book.

        :param config: The retention period of a preview, which a collection is started with.
        :type config: ProcessingConfig
        """
        self._config = config

    async def enqueue(self, book: Book, kind: JobKind, params: MetadataMap) -> Job:
        """Put a job that processes the versions of the book on its queue, behind the jobs the book has.

        A book has one run, preview or measure at a time, and one tile cutting or collection at a time. A job whose
        own group is free is queued whatever the other group is doing, and runs after the job before it.

        A run or a measure of the book does not wait for a preview of the book, queued or running, nor is it refused
        by one: the preview is cancelled the way an account holder cancels a job, after the new job is put, and the
        request goes on as if the book had been free of it. A preview that was still queued never runs. One that is
        running stops at the boundary of its current task, and the new job starts after that, so the two never overlap.
        A preview asked for while a run or a measure is active, and a run or a measure asked for while another run or
        measure is, are refused. A measure takes a preview's place like a run does, as the measure is the other button
        the reader presses while the editor of the step is looking at the page.

        :param book: The open book the job works on.
        :type book: Book
        :param kind: What the job does, one of the processing kinds.
        :type kind: JobKind
        :param params: What the job was asked to do, in the form its value class writes.
        :type params: MetadataMap
        :returns: The job as the queue holds it, queued.
        :rtype: Job
        :raises ConflictError: If a run, a preview or a measure is asked for while a run or a measure is queued or
                               running, or while a preview is, unless the request is a run or a measure, or a tile
                               cutting or a collection while one is.
        """
        if (job := await self._place(book, kind, params)) is None:
            raise ConflictError(BOOK_BUSY)
        return job

    async def _place(self, book: Book, kind: JobKind, params: MetadataMap) -> Job | None:
        """Check the rules of the book, put the job, and cancel the previews the job takes the place of.

        :param book: The open book the job works on.
        :type book: Book
        :param kind: What the job does.
        :type kind: JobKind
        :param params: What the job was asked to do.
        :type params: MetadataMap
        :returns: The job as the queue holds it, or None when a job of its group is active, which puts nothing.
        :rtype: Job | None
        """
        group = JobKind.requested() if kind in JobKind.requested() else JobKind.housekeeping()
        previews, others = self._active(book)
        # A job that takes the place of previews is not refused by them
        taken = previews if kind in JobKind.preemptive() else []
        if any(job.kind in group for job in [*others, *(previews if not taken else [])]):
            return None
        job = book.jobs.put(self._new_job(book, kind, params))
        await self._cancel(book, taken)
        return job

    async def free_of_previews(self, book: Book) -> list[Job]:
        """Cancel the previews of the book that are queued or running, and return the jobs that are still active.

        A preview is cancelled the way an account holder cancels a job, so a request that would be refused by a preview
        alone, a run, a measure of the book or the clear of a page, goes on as if the book had been free of it.

        :param book: The open book whose previews are cancelled.
        :type book: Book
        :returns: The queued or running run, tile cutting, collection or measure of the book, newest first, which
                  are none when the book was free of them.
        :rtype: list[Job]
        """
        previews, others = self._active(book)
        await self._cancel(book, previews)
        return others

    def collection(self) -> VersionCollection:
        """Say what a collection started now deletes: the versions nothing needs, and the previews old enough.

        :returns: The collection, whose moment is the retention of a preview before now.
        :rtype: VersionCollection
        """
        return VersionCollection(previews_older_than=datetime.now(UTC) - self._config.preview_retention)

    async def enqueue_collection(self, book: Book) -> Job | None:
        """Queue a collection of the book's old versions, unless the book is processing something.

        A preview, queued or running, does not count as processing: it is cancelled, since the collection is what the
        reader asked for and a preview is disposable. A preview younger than its retention is kept by the collection
        itself.

        :param book: The open book whose versions are collected.
        :type book: Book
        :returns: The collection job, new or the one that is queued or running already, or None when another job
                  processes the book. A run queues its collection when it ends, through ``queue_collection``.
        :rtype: Job | None
        """
        previews, others = self._active(book)
        collection = None if others else self._put_collection(book)
        await self._cancel(book, previews)
        if others:
            return others[0] if others[0].kind is JobKind.COLLECT_VERSIONS else None
        return collection

    def queue_collection(self, book: Book) -> Job | None:
        """Queue the collection a run follows with, behind the job that is running, unless a housekeeping job is active.

        :param book: The open book whose versions are collected.
        :type book: Book
        :returns: The collection job, or None when a tile cutting or a collection of the book is queued or running,
                  which a collection is not needed beside.
        :rtype: Job | None
        """
        _, others = self._active(book)
        if any(job.kind in JobKind.housekeeping() for job in others):
            return None
        return self._put_collection(book)

    async def enqueue_tiles(self, book: Book, version_ids: Sequence[PageVersionId]) -> Job | None:
        """Queue the cutting of the pyramids of versions that were just made the current ones of their stages.

        The change that made them current is committed already, so a job of another request that took the book in
        the meantime does not undo it. The viewer asks for the pyramid of a current version, and only the last step of
        a run cuts one, so the version of a step in the middle of a recipe has none until it is cut here.

        :param book: The open book whose versions are cut.
        :type book: Book
        :param version_ids: The versions, which may be empty.
        :type version_ids: Sequence[PageVersionId]
        :returns: The tile cutting job, or None when there is no version to cut or another tile cutting or collection
                  of the book is queued or running.
        :rtype: Job | None
        """
        if not version_ids:
            return None
        return await self._place(book, JobKind.CUT_TILES, TileCut(version_ids=tuple(version_ids)).to_map())

    def _active(self, book: Book) -> tuple[list[Job], list[Job]]:
        """Read the jobs of the book that process its versions and are queued or running.

        :param book: The open book whose jobs are read.
        :type book: Book
        :returns: The previews, and the other jobs, each newest first.
        :rtype: tuple[list[Job], list[Job]]
        """
        active = [job for job in book.jobs.active() if job.kind in JobKind.processing()]
        previews = [job for job in active if job.kind is JobKind.PREVIEW_STEP]
        return previews, [job for job in active if job not in previews]

    async def _cancel(self, book: Book, jobs: Sequence[Job]) -> None:
        """Cancel jobs the way an account holder cancels them, leaving out one that has ended meanwhile.

        :param book: The open book the jobs belong to.
        :type book: Book
        :param jobs: The jobs, which may be none.
        :type jobs: Sequence[Job]
        """
        for job in jobs:
            with suppress(JobFinishedError, NotFoundError):
                await book.jobs.cancel(job.id)

    def _put_collection(self, book: Book) -> Job:
        """Put a collection of the old versions of the book on its queue.

        :param book: The open book whose versions are collected.
        :type book: Book
        :returns: The queued job.
        :rtype: Job
        """
        return book.jobs.put(self._new_job(book, JobKind.COLLECT_VERSIONS, self.collection().to_map()))

    def _new_job(self, book: Book, kind: JobKind, params: MetadataMap) -> Job:
        """Build a queued job.

        :param book: The open book the job works on.
        :type book: Book
        :param kind: What the job does.
        :type kind: JobKind
        :param params: What the job was asked to do.
        :type params: MetadataMap
        :returns: The job, queued as of now.
        :rtype: Job
        """
        return Job(id=JobId(uuid4()), book_id=book.id, kind=kind, params=params, created_at=datetime.now(UTC))


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
