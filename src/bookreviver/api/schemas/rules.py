"""Schemas of the rules of a stage, which send the pages meeting a condition to a variant of the stage's recipe.

A rule is created from a condition, a recipe and, for the condition on a manual group, the label of the group. The
recipe of a rule is the only field that changes afterwards, since a rule with another condition is another rule.
"""

from typing import Self

from pydantic import model_validator

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.types import GroupLabel
from bookreviver.domain.enums import RuleCondition, Stage
from bookreviver.domain.ids import ProjectId, RecipeId, RecipeRuleId

GROUP_NEEDS_LABEL: str = 'A rule on a manual group needs the label of the group.'
LABEL_NEEDS_GROUP: str = 'Only a rule on a manual group takes a group label.'


class RecipeRuleSchema(ResponseModel):
    """A rule of a stage.

    :ivar id: Identifier of the rule.
    :ivar project_id: Project owning the rule.
    :ivar stage: Stage whose pages the rule sends to a recipe.
    :ivar condition: What a page must be for the rule to match it.
    :ivar group_label: The group a page must be in, for the condition on a manual group, and empty for any other.
    :ivar recipe_id: Recipe of the stage that processes the pages the rule matches.
    :ivar order: Place of the rule among the rules of the stage from zero, the lowest being tried first.
    """

    id: RecipeRuleId
    project_id: ProjectId
    stage: Stage
    condition: RuleCondition
    group_label: str
    recipe_id: RecipeId
    order: int


class RecipeRuleBody(RequestModel):
    """A rule to add after the others of the stage; the stage is in the address.

    :ivar condition: What a page must be for the rule to match it.
    :ivar group_label: The group a page must be in, given for the condition on a manual group and for no other.
    :ivar recipe_id: Recipe of the stage that processes the pages the rule matches.
    """

    condition: RuleCondition
    group_label: GroupLabel = ''
    recipe_id: RecipeId

    @model_validator(mode='after')
    def _label_belongs_to_the_group_condition(self) -> Self:
        """Check that a label is given for the condition on a manual group, and for no other.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If the condition on the group has no label, or another condition has one.
        """
        is_group = self.condition is RuleCondition.GROUP
        if is_group and not self.group_label:
            raise ValueError(GROUP_NEEDS_LABEL)
        if self.group_label and not is_group:
            raise ValueError(LABEL_NEEDS_GROUP)
        return self


class RecipeRuleTarget(RequestModel):
    """The recipe a rule sends its pages to from now on.

    :ivar recipe_id: Recipe of the stage that processes the pages the rule matches.
    """

    recipe_id: RecipeId
