"""The order of the steps of a recipe, as the processors ask for it in their specs.

A processor says where its step usually stands (``after``, ``before``) and where it must stand (``requires_after``)
relative to the steps of other processors. A step that stands off its usual place is a warning the interface shows, and
it may stay. A step that stands where it cannot work is refused when the recipe is saved, unless the recipe is saved in
the free order, which turns the refusal into the same kind of warning.

The rules name processors, and a recipe may hold two steps of one processor, so a rule is broken when any step of the
other processor stands on the wrong side, and the first such step is the one the issue names. A processor the catalogue
does not offer has no rules, and a rule that names one has nothing to compare with.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import OrderMode, OrderRuleKind
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.values import OrderIssue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.values import Step
    from bookreviver.ports.processing import ProcessorCatalog


class RecipeOrder:
    """Finds the steps of a recipe that stand off their processors' places, and refuses the impossible ones."""

    def __init__(self, catalogue: ProcessorCatalog) -> None:
        """Read the rules from the specs of a catalogue.

        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        """
        self._specs = {spec.key: spec for spec in catalogue.specs()}

    def issues(self, steps: Sequence[Step]) -> tuple[OrderIssue, ...]:
        """Find the steps that stand where their processor does not want them.

        :param steps: The steps of a recipe in the order they run, whether they are on or off.
        :type steps: Sequence[Step]
        :returns: One issue for each broken rule of each step, in the order of the steps.
        :rtype: tuple[OrderIssue, ...]
        """
        places: dict[str, list[int]] = {}
        for index, step in enumerate(steps):
            places.setdefault(step.processor_key, []).append(index)
        found: list[OrderIssue] = []
        for index, step in enumerate(steps):
            if (spec := self._specs.get(step.processor_key)) is None:
                continue
            # Each kind of rule with whether the other step has to come first, so one that comes later breaks it
            for kind, rules, other_first in (
                (OrderRuleKind.USUAL, spec.after, True),
                (OrderRuleKind.REQUIRED, spec.requires_after, True),
                (OrderRuleKind.USUAL, spec.before, False),
            ):
                for rule in rules:
                    wrong = next(
                        (other for other in places.get(rule.processor_key, ()) if (other > index) == other_first),
                        None,
                    )
                    if wrong is not None:
                        found.append(
                            OrderIssue(
                                step_id=step.step_id,
                                processor_key=step.processor_key,
                                kind=kind,
                                other_step_id=steps[wrong].step_id,
                                other_key=rule.processor_key,
                                reason=rule.reason,
                            )
                        )
        return tuple(found)

    def enforce(self, steps: Sequence[Step], order: OrderMode) -> None:
        """Refuse steps that stand where they cannot work, unless the order is free.

        :param steps: The steps of a recipe in the order they run.
        :type steps: Sequence[Step]
        :param order: Whether a required place is kept or only warned of.
        :type order: OrderMode
        :raises InvalidParametersError: If the order is the usual one and a step stands off a required place. The
                                        message gives the reason of each.
        """
        if order is OrderMode.FREE:
            return
        reasons = dict.fromkeys(issue.reason for issue in self.issues(steps) if issue.kind is OrderRuleKind.REQUIRED)
        if reasons:
            raise InvalidParametersError(' '.join(reasons))
