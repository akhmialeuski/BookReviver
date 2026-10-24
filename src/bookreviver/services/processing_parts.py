"""The collaborators every processing use case is built from, and the starting of the jobs that do the heavy work.

A use case of processing writes recipes and stage records, follows jobs and starts new ones. ``ProcessingParts`` gathers
the objects that do these over one unit of work, so ``ProcessingService`` and ``ProcessingJobs`` take one value for them
and are built the same way.

A job is put on the job board of the process, which queues it behind the jobs its book has already and runs one job of a
book at a time. A book processes one thing at a time. The user asks for a run, a preview or a measure of the book, of
which the book has one, and the application queues a tile cutting and a collection of old versions for itself, of
which it has one too. A request for a second of the first kind is refused, except that a run or a measure takes the
book from a preview by cancelling it, since a preview is a look that changes nothing. A request that comes while a
job of the other kind is active is queued behind it, so the tile cutting of the viewer never refuses a run and a run
never waits on a collection with a 409. The collection that every run queues is put behind the run when it ends, and is
left out while a housekeeping job is active.

The rules are checked against the jobs of the board and the job is put with no ``await`` in between, so no other request
can put a job between the check and the put. A cancelled preview that is running keeps its place until its current task
has returned, and the job put behind it starts only then.
"""

from contextlib import suppress
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

    from bookreviver.domain.ids import BookId, PageVersionId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock, EventPublisher, JobBoard
    from bookreviver.ports.tracing import Tracer
    from bookreviver.services.recipes import DefaultRecipes

BOOK_BUSY: str = (
    'The book is busy with another run, preview, measure of the book, tile cutting or collection of old versions. '
    'Wait for it to end, or cancel it.'
)


@frozen(kw_only=True)
class ProcessingRuntime:
    """What a processing use case reports through.

    :ivar publisher: Publisher of the events the browser follows.
    :ivar clock: Clock stamping recipes, versions and jobs.
    :ivar board: Board holding the jobs of every book and running them.
    :ivar tracer: Tracer the runs of stages are timed with.
    """

    publisher: EventPublisher
    clock: Clock
    board: JobBoard
    tracer: Tracer


@frozen(kw_only=True)
class ProcessingConfig:
    """The bounds of processing, from the settings.

    :ivar preview_retention: How long a preview is kept before a collection may delete it.
    :ivar preview_long_side_px: Longer side of a preview in pixels.
    """

    preview_retention: timedelta
    preview_long_side_px: int


class JobStarter:
    """Puts the jobs of a book on the board, under the rules of what a book may process at once."""

    def __init__(self, *, runtime: ProcessingRuntime, config: ProcessingConfig) -> None:
        """Start jobs through the board.

        :param runtime: The publisher, the clock and the board.
        :type runtime: ProcessingRuntime
        :param config: The retention period of a preview, which a collection is started with.
        :type config: ProcessingConfig
        """
        self._board = runtime.board
        self._clock = runtime.clock
        self._config = config

    async def enqueue(self, book_id: BookId, kind: JobKind, params: MetadataMap) -> Job:
        """Put a job that processes the versions of the book on the board, behind the jobs the book has.

        A book has one run, preview or measure at a time, and one tile cutting or collection at a time. A job whose
        own group is free is queued whatever the other group is doing, and runs after the job before it.

        A run or a measure of the book does not wait for a preview of the book, queued or running, nor is it refused
        by one: the preview is cancelled the way an account holder cancels a job, after the new job is put, and the
        request goes on as if the book had been free of it. A preview that was still queued never runs. One that is
        running stops at the boundary of its current task, and the new job starts after that, so the two never overlap.
        A preview asked for while a run or a measure is active, and a run or a measure asked for while another run or
        measure is, are refused. A measure takes a preview's place like a run does, as the measure is the other button
        the reader presses while the editor of the step is looking at the page.

        :param book_id: Book the job works on.
        :type book_id: BookId
        :param kind: What the job does, one of the processing kinds.
        :type kind: JobKind
        :param params: What the job was asked to do, in the form its value class writes.
        :type params: MetadataMap
        :returns: The job as the board holds it, queued.
        :rtype: Job
        :raises ConflictError: If a run, a preview or a measure is asked for while a run or a measure is queued or
                               running, or while a preview is, unless the request is a run or a measure, or a tile
                               cutting or a collection while one is.
        """
        if (job := await self._place(book_id, kind, params)) is None:
            raise ConflictError(BOOK_BUSY)
        return job

    async def _place(self, book_id: BookId, kind: JobKind, params: MetadataMap) -> Job | None:
        """Check the rules of the book, put the job, and cancel the previews the job takes the place of.

        :param book_id: Book the job works on.
        :type book_id: BookId
        :param kind: What the job does.
        :type kind: JobKind
        :param params: What the job was asked to do.
        :type params: MetadataMap
        :returns: The job as the board holds it, or None when a job of its group is active, which puts nothing.
        :rtype: Job | None
        """
        group = JobKind.requested() if kind in JobKind.requested() else JobKind.housekeeping()
        previews, others = self._active(book_id)
        # A job that takes the place of previews is not refused by them
        taken = previews if kind in JobKind.preemptive() else []
        if any(job.kind in group for job in [*others, *(previews if not taken else [])]):
            return None
        job = self._board.put(self._new_job(book_id, kind, params))
        await self._cancel(taken)
        return job

    async def free_of_previews(self, book_id: BookId) -> list[Job]:
        """Cancel the previews of the book that are queued or running, and return the jobs that are still active.

        A preview is cancelled the way an account holder cancels a job, so a request that would be refused by a preview
        alone, a run, a measure of the book or the clear of a page, goes on as if the book had been free of it.

        :param book_id: Book whose previews are cancelled.
        :type book_id: BookId
        :returns: The queued or running run, tile cutting, collection or measure of the book, newest first, which
                  are none when the book was free of them.
        :rtype: list[Job]
        """
        previews, others = self._active(book_id)
        await self._cancel(previews)
        return others

    def collection(self) -> VersionCollection:
        """Say what a collection started now deletes: the versions nothing needs, and the previews old enough.

        :returns: The collection, whose moment is the retention of a preview before now.
        :rtype: VersionCollection
        """
        return VersionCollection(previews_older_than=self._clock.now() - self._config.preview_retention)

    async def enqueue_collection(self, book_id: BookId) -> Job | None:
        """Queue a collection of the book's old versions, unless the book is processing something.

        A preview, queued or running, does not count as processing: it is cancelled, since the collection is what the
        reader asked for and a preview is disposable. A preview younger than its retention is kept by the collection
        itself.

        :param book_id: Book whose versions are collected.
        :type book_id: BookId
        :returns: The collection job, new or the one that is queued or running already, or None when another job
                  processes the book. A run queues its collection when it ends, through ``queue_collection``.
        :rtype: Job | None
        """
        previews, others = self._active(book_id)
        collection = None if others else self._put_collection(book_id)
        await self._cancel(previews)
        if others:
            return others[0] if others[0].kind is JobKind.COLLECT_VERSIONS else None
        return collection

    def queue_collection(self, book_id: BookId) -> Job | None:
        """Queue the collection a run follows with, behind the job that is running, unless a housekeeping job is active.

        :param book_id: Book whose versions are collected.
        :type book_id: BookId
        :returns: The collection job, or None when a tile cutting or a collection of the book is queued or running,
                  which a collection is not needed beside.
        :rtype: Job | None
        """
        _, others = self._active(book_id)
        if any(job.kind in JobKind.housekeeping() for job in others):
            return None
        return self._put_collection(book_id)

    async def enqueue_tiles(self, book_id: BookId, version_ids: Sequence[PageVersionId]) -> Job | None:
        """Queue the cutting of the pyramids of versions that were just made the current ones of their stages.

        The change that made them current is committed already, so a job of another request that took the book in
        the meantime does not undo it. The viewer asks for the pyramid of a current version, and only the last step of
        a run cuts one, so the version of a step in the middle of a recipe has none until it is cut here.

        :param book_id: Book whose versions are cut.
        :type book_id: BookId
        :param version_ids: The versions, which may be empty.
        :type version_ids: Sequence[PageVersionId]
        :returns: The tile cutting job, or None when there is no version to cut or another tile cutting or collection
                  of the book is queued or running.
        :rtype: Job | None
        """
        if not version_ids:
            return None
        return await self._place(book_id, JobKind.CUT_TILES, TileCut(version_ids=tuple(version_ids)).to_map())

    def _active(self, book_id: BookId) -> tuple[list[Job], list[Job]]:
        """Read the jobs of the book that process its versions and are queued or running.

        :param book_id: Book whose jobs are read.
        :type book_id: BookId
        :returns: The previews, and the other jobs, each newest first.
        :rtype: tuple[list[Job], list[Job]]
        """
        active = [job for job in self._board.jobs(book_id, active=True) if job.kind in JobKind.processing()]
        previews = [job for job in active if job.kind is JobKind.PREVIEW_STEP]
        return previews, [job for job in active if job not in previews]

    async def _cancel(self, jobs: Sequence[Job]) -> None:
        """Cancel jobs the way an account holder cancels them, leaving out one that has ended meanwhile.

        :param jobs: The jobs, which may be none.
        :type jobs: Sequence[Job]
        """
        for job in jobs:
            with suppress(JobFinishedError, NotFoundError):
                await self._board.cancel(job.id)

    def _put_collection(self, book_id: BookId) -> Job:
        """Put a collection of the old versions of the book on the board.

        :param book_id: Book whose versions are collected.
        :type book_id: BookId
        :returns: The queued job.
        :rtype: Job
        """
        return self._board.put(self._new_job(book_id, JobKind.COLLECT_VERSIONS, self.collection().to_map()))

    def _new_job(self, book_id: BookId, kind: JobKind, params: MetadataMap) -> Job:
        """Build a queued job.

        :param book_id: Book the job works on.
        :type book_id: BookId
        :param kind: What the job does.
        :type kind: JobKind
        :param params: What the job was asked to do.
        :type params: MetadataMap
        :returns: The job, queued as of now.
        :rtype: Job
        """
        return Job(id=JobId(uuid4()), book_id=book_id, kind=kind, params=params, created_at=self._clock.now())


@frozen(kw_only=True)
class ProcessingParts:
    """The objects a processing use case works through, built over one unit of work.

    :ivar recipes: The recipes of the book and the check of their steps.
    :ivar records: Writer of the stage records.
    :ivar starter: Putter of new jobs on the board.
    :ivar clock: Clock stamping what a use case writes, such as the changes a run makes to the pages.
    """

    recipes: RecipeBook
    records: StageRecords
    starter: JobStarter
    clock: Clock

    @classmethod
    def build(
        cls,
        uow: UnitOfWork,
        catalogue: ProcessorCatalog,
        defaults: DefaultRecipes,
        runtime: ProcessingRuntime,
        config: ProcessingConfig,
    ) -> ProcessingParts:
        """Build the parts over a unit of work.

        :param uow: Unit of work of the request or job.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param defaults: The recipes a stage starts with.
        :type defaults: DefaultRecipes
        :param runtime: The publisher, the clock and the board.
        :type runtime: ProcessingRuntime
        :param config: The retention period and the size of a preview.
        :type config: ProcessingConfig
        :returns: The parts.
        :rtype: ProcessingParts
        """
        return cls(
            recipes=RecipeBook(
                uow=uow, catalogue=catalogue, defaults=defaults, clock=runtime.clock, order=RecipeOrder(catalogue)
            ),
            records=StageRecords(),
            starter=JobStarter(runtime=runtime, config=config),
            clock=runtime.clock,
        )
