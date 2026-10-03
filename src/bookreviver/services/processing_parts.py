"""The collaborators every processing use case is built from, and the starting of the jobs that do the heavy work.

A use case of processing writes recipes and stage records, follows jobs and starts new ones. ``ProcessingParts`` gathers
the objects that do these over one unit of work, so ``ProcessingService`` and ``ProcessingJobs`` take one value for them
and are built the same way.

A job is recorded and committed before it is queued, so the worker finds it. A queue that refuses it leaves the job
stored as failed and announced, which tells the client it never ran, and the request that asked for it still answers,
since the rows it wrote are committed. A project processes one thing at a time, a run, a preview, a tile cutting, a
collection of its old versions or a measure of the book, so a request for a second is refused and the collection that
every run queues when it ends is left out while something else is processing the project.
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
from bookreviver.domain.values import VersionCollection
from bookreviver.services.job_runs import JobTracker
from bookreviver.services.recipes import RecipeBook
from bookreviver.services.stage_records import StageRecords

if TYPE_CHECKING:
    from datetime import timedelta

    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.services.recipes import DefaultRecipes

NOT_QUEUED: str = 'The job could not be queued.'
PROJECT_BUSY: str = (
    'The project is processing something. Wait for the run, preview, tile cutting, collection or measure of the book '
    'that is queued or running to end, or cancel it.'
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
        """Record a job that processes the versions of the project and queue it, if the project is not busy.

        A project processes one thing at a time. The check is a read and the insert a second step, so the unique index
        of the database decides when two requests pass the check together, and the one that loses is refused like the
        one that found the project busy.

        :param project_id: Project the job works on.
        :type project_id: ProjectId
        :param kind: What the job does, one of the processing kinds.
        :type kind: JobKind
        :param params: What the job was asked to do, in the form its value class writes.
        :type params: MetadataMap
        :returns: The job as stored, queued or failed.
        :rtype: Job
        :raises ConflictError: If a run, a preview, a tile cutting, a collection or a measure of the project is queued
                               or running.
        """
        if await self.busy(project_id) is not None:
            raise ConflictError(PROJECT_BUSY)
        job = Job(id=JobId(uuid4()), project_id=project_id, kind=kind, params=params, created_at=self._clock.now())
        try:
            await self._uow.jobs.add(job)
            await self._uow.commit()
        except ConflictError:
            await self._uow.rollback()
            raise ConflictError(PROJECT_BUSY) from None
        await self._publisher.publish(JobChanged(project_id=project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            logger.exception('The %s job %s could not be queued', kind, job.id)
            failed = evolve(job, state=JobState.FAILED, error=NOT_QUEUED, finished_at=self._clock.now())
            stored = await self._uow.jobs.update_if_state(failed, expected=(JobState.QUEUED,))
            await self._uow.commit()
            if stored is not None:
                await self._publisher.publish(JobChanged(project_id=project_id, job=stored))
                return stored
        return job

    async def enqueue_collection(self, project_id: ProjectId) -> Job | None:
        """Queue a collection of the project's old versions, unless the project is processing something.

        :param project_id: Project whose versions are collected.
        :type project_id: ProjectId
        :returns: The collection job, new or the one that is queued or running already, or None when another job
                  processes the project, which queues a collection when it ends.
        :rtype: Job | None
        """
        if (active := await self.busy(project_id)) is not None:
            return active if active.kind is JobKind.COLLECT_VERSIONS else None
        now = self._clock.now()
        collection = VersionCollection(
            older_than=now - self._config.version_retention, previews_older_than=now - self._config.preview_retention
        )
        try:
            return await self.enqueue(project_id, JobKind.COLLECT_VERSIONS, collection.to_map())
        except ConflictError:
            return None


@frozen(kw_only=True)
class ProcessingParts:
    """The objects a processing use case works through, built over one unit of work.

    :ivar recipes: The recipes of the project and the check of their steps.
    :ivar records: Writer of the stage records.
    :ivar tracker: The life of a job on a worker.
    :ivar starter: Recorder and queuer of new jobs.
    """

    recipes: RecipeBook
    records: StageRecords
    tracker: JobTracker
    starter: JobStarter

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
        return cls(
            recipes=RecipeBook(uow=uow, catalogue=catalogue, defaults=defaults, clock=runtime.clock),
            records=StageRecords(uow=uow, publisher=runtime.publisher, clock=runtime.clock),
            tracker=JobTracker(uow=uow, publisher=runtime.publisher, clock=runtime.clock),
            starter=JobStarter(uow=uow, runtime=runtime, config=config),
        )
