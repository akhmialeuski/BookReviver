"""The rules of a stage that send pages to variants of its recipe, as the account holder changes them.

A rule names a condition and a recipe of the same stage. The rules of a stage are tried in the order they were added,
and the first that matches a page decides its recipe, so a new rule is added after the others. A stage has one rule for
each condition, and one for each group label, since a second rule for the same condition could never be reached.

Changing a rule runs nothing and marks no page stale, since the rules only decide the recipe of the next run of the
stage that names none. ``RecipePicker`` applies them.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import RecipeRule
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import RecipeRuleId
from bookreviver.domain.values import Slice
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.enums import RuleCondition, Stage
    from bookreviver.domain.ids import ProjectId, RecipeId
    from bookreviver.domain.values import RecipeKey, SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.recipes import RecipeBook


class RecipeRules:
    """Lists, adds, retargets and removes the rules of the stages of the acting account's projects."""

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project, which a rule must name one of.
        :type recipes: RecipeBook
        """
        self._uow = uow
        self._recipes = recipes

    async def rules(
        self, actor: Actor, project_id: ProjectId, stage: Stage, request: SliceRequest
    ) -> Slice[RecipeRule]:
        """List the rules of a stage in the order they are tried.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The rules of the window, the first to try first, and the number of all the stage's rules.
        :rtype: Slice[RecipeRule]
        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        rules = await self._uow.recipe_rules.list_for_stage(project_id, stage)
        return Slice(items=rules[request.offset : request.offset + request.limit], total=len(rules))

    async def add(
        self, actor: Actor, project_id: ProjectId, target: RecipeKey, *, condition: RuleCondition, group_label: str
    ) -> RecipeRule:
        """Add a rule after the others of the stage.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param target: The stage the rule belongs to and the recipe of that stage that processes the pages it matches.
        :type target: RecipeKey
        :param condition: What a page must be for the rule to match it.
        :type condition: RuleCondition
        :param group_label: The group a page must be in, for the condition on the group, and empty for any other.
        :type group_label: str
        :returns: The rule as stored.
        :rtype: RecipeRule
        :raises NotFoundError: If the actor has no such project, or the project has no such recipe of the stage.
        :raises ConflictError: If the stage has a rule for the condition and the group label already.
        :raises ValueError: If the condition on the group has no label, or another condition has one.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._recipes.get(project_id, target.recipe_id, stage=target.stage)
        existing = await self._uow.recipe_rules.list_for_stage(project_id, target.stage)
        rule = RecipeRule(
            id=RecipeRuleId(uuid4()),
            project_id=project_id,
            stage=target.stage,
            condition=condition,
            group_label=group_label,
            recipe_id=target.recipe_id,
            order=max((one.order for one in existing), default=-1) + 1,
        )
        stored = await self._uow.recipe_rules.add(rule)
        await self._uow.commit()
        return stored

    async def retarget(
        self, actor: Actor, project_id: ProjectId, stage: Stage, rule_id: RecipeRuleId, recipe_id: RecipeId
    ) -> RecipeRule:
        """Send the pages a rule matches to another recipe of the stage, keeping the place of the rule.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage in the address of the request.
        :type stage: Stage
        :param rule_id: Identifier of the rule.
        :type rule_id: RecipeRuleId
        :param recipe_id: Recipe of the stage that processes the pages from now on.
        :type recipe_id: RecipeId
        :returns: The rule as stored.
        :rtype: RecipeRule
        :raises NotFoundError: If the actor has no such project, the stage has no such rule, or the project has no such
                               recipe of the stage.
        """
        await owned_project(self._uow.projects, actor, project_id)
        rule = await self._rule(project_id, stage, rule_id)
        await self._recipes.get(project_id, recipe_id, stage=stage)
        stored = await self._uow.recipe_rules.update(evolve(rule, recipe_id=recipe_id))
        await self._uow.commit()
        return stored

    async def remove(self, actor: Actor, project_id: ProjectId, stage: Stage, rule_id: RecipeRuleId) -> None:
        """Remove a rule, so the pages it matched fall to the rules after it, or to the active recipe.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage in the address of the request.
        :type stage: Stage
        :param rule_id: Identifier of the rule.
        :type rule_id: RecipeRuleId
        :raises NotFoundError: If the actor has no such project, or the stage has no such rule.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._rule(project_id, stage, rule_id)
        await self._uow.recipe_rules.delete(rule_id)
        await self._uow.commit()

    async def _rule(self, project_id: ProjectId, stage: Stage, rule_id: RecipeRuleId) -> RecipeRule:
        """Find a rule of a stage of a project.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage the rule must belong to.
        :type stage: Stage
        :param rule_id: Identifier of the rule.
        :type rule_id: RecipeRuleId
        :returns: The rule.
        :rtype: RecipeRule
        :raises NotFoundError: If the rule is not stored, or belongs to another project or stage.
        """
        rule = await self._uow.recipe_rules.get(rule_id)
        if rule.project_id != project_id or rule.stage is not stage:
            raise NotFoundError(rule_id)
        return rule
