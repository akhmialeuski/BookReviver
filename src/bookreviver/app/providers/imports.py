"""Provider of the import feature: the Taskiq broker, the job queue and the job service.

The broker is an application-scoped generator, so it is started when first needed and stopped when the container
closes, after the jobs running in this process have finished. Starting it in the application's lifespan instead would
split its assembly between two places, while the provider already knows the container the tasks resolve their
services from.

The import service joins this provider together with the import task in ``app/worker.py``.
"""

from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, provide
from taskiq import AsyncBroker

from bookreviver.adapters.jobs.taskiq_queue import TaskiqJobQueue
from bookreviver.app.settings import Settings
from bookreviver.app.worker import create_broker, stop_broker
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream, JobQueue
from bookreviver.services.jobs import JobService


class ImportsProvider(Provider):
    """Builds the broker and the job queue once per application, and the job service once per request or job."""

    @provide(scope=Scope.APP)
    async def broker(self, settings: Settings, container: AsyncContainer) -> AsyncIterator[AsyncBroker]:
        """Start the broker the settings select, and stop it when the application closes the container.

        :param settings: Application settings, of which ``job_broker`` is read.
        :type settings: Settings
        :param container: Application container, which opens a request scope for every task.
        :type container: AsyncContainer
        :returns: Iterator yielding the started broker and stopping it afterwards.
        :rtype: AsyncIterator[AsyncBroker]
        """
        broker = create_broker(settings.job_broker, container)
        await broker.startup()
        # try/finally rather than a context manager around the yield, because dishka drives this generator
        try:
            yield broker
        finally:
            await stop_broker(broker)

    @provide(scope=Scope.APP)
    def job_queue(self, broker: AsyncBroker) -> JobQueue:
        """Build the queue that hands jobs to the broker.

        :param broker: The started broker.
        :type broker: AsyncBroker
        :returns: Queue kicking the task registered under each job's kind.
        :rtype: JobQueue
        """
        return TaskiqJobQueue(broker)

    @provide(scope=Scope.REQUEST)
    def job_service(self, uow: UnitOfWork, publisher: EventPublisher, stream: EventStream, clock: Clock) -> JobService:
        """Build the job service over the unit of work of the request or job.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :param publisher: Publisher of the application's event bus.
        :type publisher: EventPublisher
        :param stream: Stream of the application's event bus.
        :type stream: EventStream
        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The job service.
        :rtype: JobService
        """
        return JobService(uow=uow, publisher=publisher, stream=stream, clock=clock)
