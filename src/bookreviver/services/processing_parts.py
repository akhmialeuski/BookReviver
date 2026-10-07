"""The collaborators every processing use case is built from, and the starting of the jobs that do the heavy work.

A use case of processing writes recipes and stage records, follows jobs and starts new ones. ``ProcessingParts`` gathers
the objects that do these over one unit of work, so ``ProcessingService`` and ``ProcessingJobs`` take one value for them
and are built the same way.

A job is recorded and committed before it is queued, so the worker finds it. A queue that refuses it leaves the job
stored as failed and announced, which tells the client it never ran, and the request that asked for it still answers,
since the rows it wrote are committed. A project processes one thing at a time. The user asks for a run, a preview or a
measure of the book, of which the project has one, and the application queues a tile cutting and a collection of old
versions for itself, of which it has one too. A request for a second of the first kind is refused, except that a run or
a measure takes the project from a preview by cancelling it, since a preview is a look that changes nothing. A request
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

    :ivar version_retention: How long a full run that is not current is kept before a collection may delete it.
    :ivar preview_retention: How long a preview is kept before a collection may delete it.
    :ivar preview_long_side_px: Longer side of a preview in pixels.
    """

    version_retention: timedelta
    preview_retention: timedelta
    preview_long_side_px: int


class JobStarter:
    """Records jobs of a project and queues them."""

    def __init__(self, *, uow: UnitOfWork, runtime: ProcessingRuntime, config: ProcessingConfig) -> None:
        """Start jobs through the unit of work and the queue.

        :param uow: Unit of work, committed when a job is recorded.
        :type uow: UnitOfWork
        :param runtime: The publisher, the clock and the queue.
        :type runtime: ProcessingRuntime
        :param config: The retention periods, which a collection is started with.
        :type config: ProcessingConfig
        """
        self._uow = uow
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._queue = runtime.queue
        self._config = config
        self._cancellation = JobCancellation(uow=uow, publisher=runtime.publisher, clock=runtime.clock)

    async def busy(self, project_id: ProjectId) -> Job | None:
        """Return the job that is processing the versions of the project, if one is queued or running.

        :param project_id: Project whose jobs are read.
        :type project_id: ProjectId
        :returns: The newest queued or running run, preview, tile cutting, collection or measure, or None.
        :rtype: Job | None
        """
        active = await self._uow.jobs.list_for_project(project_id, JobState.active())
        return next((job for job in active if job.kind in JobKind.processing()), None)

    async def enqueue(self, project_id: ProjectId, kind: JobKind, params: MetadataMap) -> Job:
        """Record a job that processes the versions of the project, and queue it unless another job goes first.

        A project has one run, preview or measure at a time, and one tile cutting or collection at a time, and only one
        of them runs. A job whose own group is free is stored whatever the other group is doing. It is queued to a
        worker at once when no job of the project is active, and otherwise it waits as queued until ``hand_off``
        queues it when the job before it ends. The check is a read and the insert a second step, so the unique indexes
        of the database decide when two requests pass the check together, and the one that loses is refused like the
        one that found its group taken.

        A run or a measure of the book does not wait for a preview of the project, queued or running, nor is it refused
        by one: the preview is cancelled the way an account holder cancels a job, committed and announced, and the
        request goes on as if the project had been free of it. This is safe for the preview whose worker has begun,
        because that worker never reads its own state before the end of its steps. It goes on to make the preview of the
        steps it started, which are versions of the preview scale under identifiers that name that scale and which
        nothing current or full-scale refers to, so it writes no version the run or the measure reads or writes. When it
        ends, its final write finds the job cancelled and changes nothing, and ``hand_off`` queues only a job stored
        before the cancellation, which the run, stored after it, is not, so the run is queued to a worker once, here. A
        preview that was still queued is never started, since a worker that takes a finished job passes it by. A
        preview asked for while a run or a measure is active, and a run or a measure asked for while another run or
        measure is, are refused as before. A preview that another request stores after the check and before the insert
        makes the insert fail on the unique index, and a run or a measure then reads the project again once, to cancel
        that preview too, before it is refused like a request that lost the race to a run. A measure takes a preview's
        place like a run does, as the measure is the other button the reader presses while the editor of the step is
        looking at the page.

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
        try:
            job, active = await self._store(project_id, kind, params)
        except ConflictError:
            if kind not in JobKind.preemptive():
                raise
            # A preview stored between the check and the insert took the place the insert needs, which the unique index
            # tells, so the project is read once more, and the preview cancelled too
            job, active = await self._store(project_id, kind, params)
        if active:
            await self._publisher.publish(JobChanged(project_id=project_id, job=job))
            return job
        return await self.dispatch(job)

    async def _store(self, project_id: ProjectId, kind: JobKind, params: MetadataMap) -> tuple[Job, list[Job]]:
        """Read the project, free it of the previews a run or a measure takes it from, and store the job.

        :param project_id: Project the job works on.
        :type project_id: ProjectId
        :param kind: What the job does.
        :type kind: JobKind
        :param params: What the job was asked to do.
        :type params: MetadataMap
        :returns: The job as committed, and the jobs that were active when it was stored.
        :rtype: tuple[Job, list[Job]]
        :raises ConflictError: If a job of its group is active, or another request took the group before the insert.
        """
        group = JobKind.requested() if kind in JobKind.requested() else JobKind.housekeeping()
        active = await self._active(project_id)
        if kind in JobKind.preemptive():
            # A preview that has ended since it was read is not in the way either, so a cancellation that finds it ended
            # is no failure
            previews = [other for other in active if other.kind is JobKind.PREVIEW_STEP]
            for preview in previews:
                await self._cancellation.cancel(preview)
            active = [other for other in active if other not in previews]
        if any(other.kind in group for other in active):
            raise ConflictError(PROJECT_BUSY)
        job = Job(id=JobId(uuid4()), project_id=project_id, kind=kind, params=params, created_at=self._clock.now())
        try:
            await self._uow.jobs.add(job)
            await self._uow.commit()
        except ConflictError:
            await self._uow.rollback()
            raise ConflictError(PROJECT_BUSY) from None
        return job, active

    async def hand_off(self, ended: Job, collection: Job | None) -> None:
        """Queue to a worker the job the project goes on with after a job that processes its versions has ended.

        That is the collection stored with the end of a run, or else the oldest job that waits as queued, unless a job
        of the project is running already. A job waits when it was stored before the ended job ended, since one stored
        later found the project free and was queued to a worker at once. The end of a job is when it was finished or
        cancelled, as the database holds it, because an account holder may cancel a running job, which frees the
        project before its worker notices.

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

        A queue that refuses the job leaves it stored as failed and announced, so the client learns it never ran.

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
            stored = await self._uow.jobs.update_if_state(failed, expected=(JobState.QUEUED,))
            await self._uow.commit()
            if stored is not None:
                await self._publisher.publish(JobChanged(project_id=job.project_id, job=stored))
                return stored
        return job

    def new_collection(self, project_id: ProjectId) -> Job:
        """Build the job that collects the old versions of a project, which is not stored yet.

        :param project_id: Project whose versions are collected.
        :type project_id: ProjectId
        :returns: The queued job, to store with the end of the job that precedes it, or by ``enqueue``.
        :rtype: Job
        """
        now = self._clock.now()
        collection = VersionCollection(
            older_than=now - self._config.version_retention, previews_older_than=now - self._config.preview_retention
        )
        return Job(
            id=JobId(uuid4()),
            project_id=project_id,
            kind=JobKind.COLLECT_VERSIONS,
            params=collection.to_map(),
            created_at=now,
        )

    async def enqueue_collection(self, project_id: ProjectId) -> Job | None:
        """Queue a collection of the project's old versions, unless the project is processing something.

        :param project_id: Project whose versions are collected.
        :type project_id: ProjectId
        :returns: The collection job, new or the one that is queued or running already, or None when another job
                  processes the project, which queues a collection when it ends. A run queues its collection with its
                  own end, through ``JobTracker.finish``, and not through this method.
        :rtype: Job | None
        """
        if (active := await self.busy(project_id)) is not None:
            return active if active.kind is JobKind.COLLECT_VERSIONS else None
        try:
            return await self.enqueue(project_id, JobKind.COLLECT_VERSIONS, self.new_collection(project_id).params)
        except ConflictError:
            return None

    async def enqueue_tiles(self, project_id: ProjectId, version_ids: Sequence[PageVersionId]) -> Job | None:
        """Queue the cutting of the pyramids of versions that were just made the current ones of their stages.

        The change that made them current is committed already, so a job of another request that took the project in
        the meantime does not undo it. The viewer asks for the pyramid of a current version, and only the last step of
        a run cuts one, so the version of a step in the middle of a recipe has none until it is cut here.

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
        try:
            return await self.enqueue(project_id, JobKind.CUT_TILES, TileCut(version_ids=tuple(version_ids)).to_map())
        except ConflictError:
            return None


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
        :param config: The retention periods and the size of a preview.
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
