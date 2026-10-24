"""Building the services that work on many pages at once, and calling them for the values of one page."""

from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.domain.enums import ValueScope
from bookreviver.domain.step_values import ValueField, ValueTarget
from bookreviver.services.page_carry import CarryOverService
from bookreviver.services.run_plans import RunImpactService
from tests.helpers.builders import open_for

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.history import ValueChanges
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.values import PageStepKey
    from tests.helpers.processing import ProcessingKit


def carry_over(kit: ProcessingKit) -> CarryOverService:
    """Build the carry-over service over a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: CarryOverService
    """
    return CarryOverService(records=kit.parts().records)


def run_impact(kit: ProcessingKit) -> RunImpactService:
    """Build the service that counts what a run would take away, over a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The service.
    :rtype: RunImpactService
    """
    return RunImpactService(recipes=kit.parts().recipes)


def _page_field(key: PageStepKey, name: str) -> ValueField:
    """Name a field of a step together with the one page a value of it is for.

    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :param name: Name of the field.
    :type name: str
    :returns: The field.
    :rtype: ValueField
    """
    return ValueField(
        stage=key.stage,
        step_id=key.step_id,
        name=name,
        target=ValueTarget(scope=ValueScope.PAGES, page_ids=(key.page_id,)),
    )


@frozen
class PageValues:
    """Sets and takes back the value one page uses for a field of a step, each through a new unit of work.

    :ivar kit: What the processing services of the test share.
    :ivar actor: Account acting.
    :ivar book_id: The book.
    """

    kit: ProcessingKit
    actor: Actor
    book_id: BookId

    async def set(self, key: PageStepKey, name: str, value: object) -> ValueChanges:
        """Set the value the page uses for a field of a step.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :param name: Name of the field.
        :type name: str
        :param value: The value the page uses.
        :type value: object
        :returns: The batch and the changes written.
        :rtype: ValueChanges
        """
        return await self.kit.page_settings().change(
            open_for(self.actor, self.book_id, databases=self.kit.databases, files=self.kit.files),
            _page_field(key, name),
            value,
        )

    async def take_back(self, key: PageStepKey, name: str) -> ValueChanges:
        """Take the value the page uses for a field of a step back.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :param name: Name of the field.
        :type name: str
        :returns: The batch and the changes written.
        :rtype: ValueChanges
        """
        return await self.kit.page_settings().reset(
            open_for(self.actor, self.book_id, databases=self.kit.databases, files=self.kit.files),
            _page_field(key, name),
        )
