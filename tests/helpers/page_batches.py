"""Building the services that work on many pages at once, over the unit of work of a new request."""

from typing import TYPE_CHECKING

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.services.page_carry import CarryOverService
from bookreviver.services.run_plans import RunImpactService
from bookreviver.services.step_resets import StepResetService

if TYPE_CHECKING:
    from tests.helpers.processing import ProcessingKit


def carry_over(kit: ProcessingKit) -> CarryOverService:
    """Build the carry-over service over a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: CarryOverService
    """
    uow = InMemoryUnitOfWork(kit.database)
    return CarryOverService(uow=uow, catalogue=kit.catalogue, records=kit.parts(uow).records, clock=kit.clock)


def run_impact(kit: ProcessingKit) -> RunImpactService:
    """Build the service that counts what a run would take away, over a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: RunImpactService
    """
    uow = InMemoryUnitOfWork(kit.database)
    return RunImpactService(uow=uow, recipes=kit.parts(uow).recipes, clock=kit.clock)


def step_resets(kit: ProcessingKit) -> StepResetService:
    """Build the service of the resets of steps over a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: StepResetService
    """
    uow = InMemoryUnitOfWork(kit.database)
    return StepResetService(uow=uow, records=kit.parts(uow).records, clock=kit.clock)
