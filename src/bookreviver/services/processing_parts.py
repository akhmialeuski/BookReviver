"""The collaborators every processing use case is built from, and the starting of the jobs that do the heavy work.

A use case of processing writes recipes and stage records, follows jobs and starts new ones. ``ProcessingParts`` gathers
the objects that do these over one unit of work, so ``ProcessingService`` and ``ProcessingJobs`` take one value for them
and are built the same way.

A job is recorded in a block that has committed before it is queued, so the worker finds it. A queue that refuses it
leaves the job stored as failed and announced, which tells the client it never ran, and the request that asked for it
still answers, since the rows it wrote are committed. A project processes one thing at a time. The user asks for a run,
a preview or a measure of the book, of which the project has one, and the application queues a tile cutting and a
collection of old versions for itself, of which it has one too. A request for a second of the first kind is refused,
except that a run or a measure takes the project from a preview by cancelling it, since a preview is a look that
changes nothing. A request
that comes while a job of the other kind is active is stored as queued and waits, and the end of that job queues it to
a worker, so the tile cutting of the viewer never refuses a run and a run never waits on a collection with a 409. The
collection that every run queues is stored with the end of the run, and is left out while another job waits.
"""

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import Job
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.events import JobChanged
from bookreviver.domain.ids import JobId
from bookreviver.domain.values import TileCut, VersionCollection
from bookreviver.services.job_runs import JobTracker
from bookreviver.services.jobs import JobCancellation
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipes import RecipeBook
from bookreviver.services.stage_records import StageRecords

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from bookreviver.domain.ids import PageVersionId, ProjectId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.services.recipes import DefaultRecipes

NOT_QUEUED: str = 'The job could not be queued.'
PROJECT_BUSY: str = (
    'The project is busy with another run, preview, measure of the book, tile cutting or collection of old versions. '
    'Wait for it to end, or cancel it.'
)

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class ProcessingRuntime:
    """What a processing use case reports through.

    :ivar publisher: Publisher of the events the browser follows.
    :ivar clock: Clock stamping recipes, versions and jobs.
    :ivar queue: Queue handing jobs to the workers.
    """

    publisher: EventPublisher
    clock: Clock
    queue: JobQueue


@frozen(kw_only=True)
class ProcessingConfig:
    """The bounds of processing, from the settings.

    :ivar preview_retention: How long a preview is kept before a collection may delete it.
    :ivar preview_long_side_px: Longer side of a preview in pixels.
    """

    preview_retention: timedelta
    preview_long_side_px: int


class JobStarter:
    """Records jobs of a project and queues them.

    Every public method that writes opens its own ``change`` block and is called only outside a block, and the one that
    only reads opens none.
    """

    def __init__(self, *, uow: UnitOfWork, runtime: ProcessingRuntime, config: ProcessingConfig) -> None:
        """Start jobs through the unit of work and the queue.

        :param uow: Unit of work, whose ``change`` block holds the recording of a job.
        :type uow: UnitOfWork
        :param runtime: The publisher, the clock and the queue.
        :type runtime: ProcessingRuntime
        :param config: The retention period of a preview, which a collection is started with.
        :type config: ProcessingConfig
        """
        self._uow = uow
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._queue = runtime.queue
        self._config = config
        self._cancellation = JobCancellation(uow=uow, publisher=runtime.publisher, clock=runtime.clock)

    async def enqueue(self, project_id: ProjectId, kind: JobKind, params: MetadataMap) -> Job:
        """Record a job that processes the versions of the project, and queue it unless another job goes first.

        A project has one run, preview or measure at a time, and one tile cutting or collection at a time, and only one
        of them runs. A job whose own group is free is stored whatever the other group is doing. It is queued to a
        worker at once when no job of the project is active, and otherwise it waits as queued until ``hand_off``
        queues it when the job before it ends. The check and the insert are one ``change`` block, so two requests
        cannot pass the check together, and the unique indexes of the database stay the last line of defence.

        A run or a measure of the book does not wait for a preview of the project, queued or running, nor is it refused
        by one: the preview is cancelled the way an account holder cancels a job, in the same block as the insert and
        announced after it, and the request goes on as if the project had been free of it. This is safe for the preview
        whose worker has begun, because that worker never reads its own state before the end of its steps. It goes on
        to make the preview of the steps it started, which are versions of the preview scale under identifiers that
        name that scale and which nothing current or full-scale refers to, so it writes no version the run or the
        measure reads or writes. When it ends, its final write finds the job cancelled and changes nothing, and
        ``hand_off`` queues only a job stored before the cancellation, which the run, stored after it, is not, so the
        run is queued to a worker once, here. A preview that was still queued is never started, since a worker that
        takes a finished job passes it by. A preview asked for while a run or a measure is active, and a run or a
        measure asked for while another run or measure is, are refused as before. A measure takes a preview's place
        like a run does, as the measure is the other button the reader presses while the editor of the step is looking
        at the page.

        :param project_id: Project the job works on.
        :type project_id: ProjectId
        :param kind: What the job does, one of the processing kinds.
        :type kind: JobKind
        :param params: What the job was asked to do, in the form its value class writes.
        :type params: MetadataMap
        :returns: The job as stored, queued, waiting or failed.
        :rtype: Job
        :raises ConflictError: If a run, a preview or a measure is asked for while a run or a measure is queued or
                               running, or while a preview is, unless the request is a run or a measure, or a tile
                               cutting or a collection while one is.
        """
        if (job := await self._place(project_id, kind, params)) is None:
            raise ConflictError(PROJECT_BUSY)
        return job

    async def _place(self, project_id: ProjectId, kind: JobKind, params: MetadataMap) -> Job | None:
        """Record a job in one block with the check of the project and the cancellation of its previews, and queue it.

        Opens its own ``change`` block, so it is called outside any block.

        :param project_id: Project the job works on.
        :type project_id: ProjectId
        :param kind: What the job does.
        :type kind: JobKind
        :param params: What the job was asked to do.
        :type params: MetadataMap
        :returns: The job as stored, queued, waiting or failed, or None when a job of its group is active, in which
                  case nothing is stored.
        :rtype: Job | None
        """
        group = JobKind.requested() if kind in JobKind.requested() else JobKind.housekeeping()
        async with self._uow.change():
            if kind in JobKind.preemptive():
                active, cancelled = await self._clear_previews(project_id)
            else:
                active, cancelled = await self._active(project_id), []
            if any(other.kind in group for other in active):
                job = None
            else:
                # Made after the cancellations, so it is stored after the end of every preview it cancelled, and the
                # hand-off at the end of such a preview does not take it for a job that waited and queue it again
                job = Job(
                    id=JobId(uuid4()), project_id=project_id, kind=kind, params=params, created_at=self._clock.now()
                )
                await self._uow.jobs.add(job)
        await self._announce(cancelled)
        if job is None:
            return None
        if active:
            await self._announce([job])
            return job
        return await self.dispatch(job)

    async def free_of_previews(self, project_id: ProjectId) -> list[Job]:
        """Cancel the previews of the project that are queued or running, and return the jobs that are still active.

        A preview is cancelled the way an account holder cancels a job, in a ``change`` block of its own and announced
        after it, so a request that would be refused by a preview alone, a run, a measure of the book or the clear of a
        page, goes on as if the project had been free of it. Opens its own block, so it is called outside any block.

        :param project_id: Project whose previews are cancelled.
        :type project_id: ProjectId
        :returns: The queued or running run, tile cutting, collection or measure of the project, newest first, which
                  are none when the project was free of them.
        :rtype: list[Job]
        """
        async with self._uow.change():
            active, cancelled = await self._clear_previews(project_id)
        await self._announce(cancelled)
        return active

    async def _clear_previews(self, project_id: ProjectId) -> tuple[list[Job], list[Job]]:
        """Cancel the previews of the project that are queued or running, and announce nothing.

        Writes inside the block of its caller and never opens one, so the caller announces the cancelled previews after
        its block committed.

        :param project_id: Project whose previews are cancelled.
        :type project_id: ProjectId
        :returns: The jobs of the project that are still active, and the previews that were cancelled, which leave out a
                  preview that had ended since it was read.
        :rtype: tuple[list[Job], list[Job]]
        """
        active = await self._active(project_id)
        previews = [other for other in active if other.kind is JobKind.PREVIEW_STEP]
        cancelled = [done for preview in previews if (done := await self._cancellation.mark_cancelled(preview))]
        return [other for other in active if other not in previews], cancelled

    async def _announce(self, jobs: Sequence[Job]) -> None:
        """Publish the state of jobs a block has committed.

        :param jobs: The jobs, which may be none.
        :type jobs: Sequence[Job]
        """
        for job in jobs:
            await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))

    async def hand_off(self, ended: Job, collection: Job | None) -> None:
        """Queue to a worker the job the project goes on with after a job that processes its versions has ended.

        That is the collection stored with the end of a run, or else the oldest job that waits as queued, unless a job
        of the project is running already. A job waits when it was stored before the ended job ended, since one stored
        later found the project free and was queued to a worker at once. The end of a job is when it was finished or
        cancelled, as the database holds it, because an account holder may cancel a running job, which frees the
        project before its worker notices. It reads outside any block and writes only through ``dispatch``, which opens
        its own, so it is called outside any block.

        :param ended: The job that ended, or that its worker found cancelled.
        :type ended: Job
        :param collection: The collection stored with the end of the job, or None.
        :type collection: Job | None
        """
        if collection is not None:
            await self.dispatch(collection)
            return
        stored = await self._uow.jobs.get(ended.id)
        cutoff = stored.finished_at or self._clock.now()
        active = await self._active(ended.project_id)
        if any(job.state is JobState.RUNNING for job in active):
            return
        if waiting := [job for job in active if job.state is JobState.QUEUED and job.created_at <= cutoff]:
            await self.dispatch(min(waiting, key=lambda job: job.created_at))

    async def _active(self, project_id: ProjectId) -> list[Job]:
        """Read the jobs of the project that process its versions and are queued or running.

        :param project_id: Project whose jobs are read.
        :type project_id: ProjectId
        :returns: The jobs, newest first.
        :rtype: list[Job]
        """
        active = await self._uow.jobs.list_for_project(project_id, JobState.active())
        return [job for job in active if job.kind in JobKind.processing()]

    async def dispatch(self, job: Job) -> Job:
        """Announce a job that is stored as queued and hand it to the queue, which a worker takes it from.

        A queue that refuses the job leaves it stored as failed and announced, so the client learns it never ran. That
        write is a ``change`` block of its own, so this is called outside any block.

        :param job: The queued job, committed already.
        :type job: Job
        :returns: The job as stored, queued or failed.
        :rtype: Job
        """
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            logger.exception('The %s job %s could not be queued', job.kind, job.id)
            failed = evolve(job, state=JobState.FAILED, error=NOT_QUEUED, finished_at=self._clock.now())
            async with self._uow.change():
                stored = await self._uow.jobs.update_if_state(failed, expected=(JobState.QUEUED,))
            if stored is not None:
                await self._publisher.publish(JobChanged(project_id=job.project_id, job=stored))
                return stored
        return job

    def collection(self) -> VersionCollection:
        """Say what a collection started now deletes: the versions nothing needs, and the previews old enough.

        :returns: The collection, whose moment is the retention of a preview before now.
        :rtype: VersionCollection
        """
        return VersionCollection(previews_older_than=self._clock.now() - self._config.preview_retention)

    def new_collection(self, project_id: ProjectId) -> Job:
        """Build the job that collects the old versions of a project, which is not stored yet.

        :param project_id: Project whose versions are collected.
        :type project_id: ProjectId
        :returns: The queued job, to store with the end of the job that precedes it, or by ``enqueue``.
        :rtype: Job
        """
        return Job(
            id=JobId(uuid4()),
            project_id=project_id,
            kind=JobKind.COLLECT_VERSIONS,
            params=self.collection().to_map(),
            created_at=self._clock.now(),
        )

    async def enqueue_collection(self, project_id: ProjectId) -> Job | None:
        """Queue a collection of the project's old versions, unless the project is processing something.

        A preview, queued or running, does not count as processing: it is cancelled first, since the collection is what
        the reader asked for and a preview is disposable. A preview younger than its retention is kept by the
        collection itself.

        :param project_id: Project whose versions are collected.
        :type project_id: ProjectId
        :returns: The collection job, new or the one that is queued or running already, or None when another job
                  processes the project, which queues a collection when it ends. A run queues its collection with its
                  own end, through ``JobTracker.finish``, and not through this method.
        :rtype: Job | None
        """
        collection = self.new_collection(project_id)
        async with self._uow.change():
            active, cancelled = await self._clear_previews(project_id)
            if not active:
                await self._uow.jobs.add(collection)
        await self._announce(cancelled)
        if active:
            return active[0] if active[0].kind is JobKind.COLLECT_VERSIONS else None
        return await self.dispatch(collection)

    async def enqueue_tiles(self, project_id: ProjectId, version_ids: Sequence[PageVersionId]) -> Job | None:
        """Queue the cutting of the pyramids of versions that were just made the current ones of their stages.

        The change that made them current is committed already, so a job of another request that took the project in
        the meantime does not undo it. The viewer asks for the pyramid of a current version, and only the last step of
        a run cuts one, so the version of a step in the middle of a recipe has none until it is cut here. Opens its own
        ``change`` block, so it is called outside any block.

        :param project_id: Project whose versions are cut.
        :type project_id: ProjectId
        :param version_ids: The versions, which may be empty.
        :type version_ids: Sequence[PageVersionId]
        :returns: The tile cutting job, or None when there is no version to cut or another tile cutting or collection
                  of the project is queued or running.
        :rtype: Job | None
        """
        if not version_ids:
            return None
        return await self._place(project_id, JobKind.CUT_TILES, TileCut(version_ids=tuple(version_ids)).to_map())


@frozen(kw_only=True)
class ProcessingParts:
    """The objects a processing use case works through, built over one unit of work.

    :ivar recipes: The recipes of the project and the check of their steps.
    :ivar records: Writer of the stage records.
    :ivar tracker: The life of a job on a worker.
    :ivar starter: Recorder and queuer of new jobs.
    :ivar clock: Clock stamping what a use case writes, such as the changes a run makes to the pages.
    """

    recipes: RecipeBook
    records: StageRecords
    tracker: JobTracker
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
        :param runtime: The publisher, the clock and the queue.
        :type runtime: ProcessingRuntime
        :param config: The retention period and the size of a preview.
        :type config: ProcessingConfig
        :returns: The parts.
        :rtype: ProcessingParts
        """
        starter = JobStarter(uow=uow, runtime=runtime, config=config)
        return cls(
            recipes=RecipeBook(
                uow=uow, catalogue=catalogue, defaults=defaults, clock=runtime.clock, order=RecipeOrder(catalogue)
            ),
            records=StageRecords(uow=uow, publisher=runtime.publisher, clock=runtime.clock),
            tracker=JobTracker(uow=uow, publisher=runtime.publisher, clock=runtime.clock, hand_off=starter.hand_off),
            starter=starter,
            clock=runtime.clock,
        )
