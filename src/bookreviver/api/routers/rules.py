"""The rules of a stage: which pages are processed by which variant of the stage's recipe.

A run of a stage that names no recipe gives each page the recipe pinned to it, else the recipe of the first rule that
matches it, else the active recipe. These routes only keep the rules. They run nothing, and the stage is run by
``POST .../run``.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import PROJECT_ID_DESCRIPTION, STAGE_DESCRIPTION, StagePath
from bookreviver.api.schemas.rules import RecipeRuleBody, RecipeRuleSchema, RecipeRuleTarget
from bookreviver.domain.entities import RecipeRule
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import ProjectId, RecipeRuleId
from bookreviver.domain.values import RecipeKey
from bookreviver.services.recipe_rules import RecipeRules

router = APIRouter(prefix='/projects', tags=['rules'], route_class=DishkaRoute)


@dataclass(frozen=True)
class RulePath:
    """The identifiers in the address of one rule of a stage of a project.

    :ivar project_id: Identifier of the project.
    :ivar stage: The stage.
    :ivar rule_id: Identifier of the rule.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    stage: Annotated[Stage, Path(description=STAGE_DESCRIPTION)]
    rule_id: Annotated[RecipeRuleId, Path(description='Identifier of the rule')]


@router.get('/{project_id}/stages/{stage}/rules')
async def list_rules(
    address: Annotated[StagePath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    rules: FromDishka[RecipeRules],
) -> Page[RecipeRuleSchema]:
    """List the rules of a stage in the order they are tried, which is the order they were added in.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param rules: Rules service of the request.
    :type rules: RecipeRules
    :returns: One page of the rules of the stage.
    :rtype: Page[RecipeRuleSchema]
    """
    pager = Pager[RecipeRule, RecipeRuleSchema](params, RecipeRuleSchema.model_validate)
    return pager.page(await rules.rules(actor, address.project_id, address.stage, pager.request))


@router.post('/{project_id}/stages/{stage}/rules', status_code=status.HTTP_201_CREATED)
async def create_rule(
    address: Annotated[StagePath, Depends()],
    body: RecipeRuleBody,
    actor: ActorDep,
    rules: FromDishka[RecipeRules],
) -> RecipeRuleSchema:
    """Add a rule after the others of the stage.

    A stage has one rule for each condition, and one for each label of a manual group, so a second rule for the same
    condition answers 409. A recipe that is not a recipe of the stage answers 404.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The condition, the label of the group for a manual group, and the recipe.
    :type body: RecipeRuleBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param rules: Rules service of the request.
    :type rules: RecipeRules
    :returns: The rule as stored.
    :rtype: RecipeRuleSchema
    """
    rule = await rules.add(
        actor,
        address.project_id,
        RecipeKey(address.stage, body.recipe_id),
        condition=body.condition,
        group_label=body.group_label,
    )
    return RecipeRuleSchema.model_validate(rule)


@router.put('/{project_id}/stages/{stage}/rules/{rule_id}')
async def put_rule(
    address: Annotated[RulePath, Depends()],
    body: RecipeRuleTarget,
    actor: ActorDep,
    rules: FromDishka[RecipeRules],
) -> RecipeRuleSchema:
    """Send the pages a rule matches to another recipe of the stage, keeping the place of the rule.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the rule.
    :type address: RulePath
    :param body: The recipe.
    :type body: RecipeRuleTarget
    :param actor: The signed-in account.
    :type actor: Actor
    :param rules: Rules service of the request.
    :type rules: RecipeRules
    :returns: The rule as stored.
    :rtype: RecipeRuleSchema
    """
    rule = await rules.retarget(actor, address.project_id, address.stage, address.rule_id, body.recipe_id)
    return RecipeRuleSchema.model_validate(rule)


@router.delete('/{project_id}/stages/{stage}/rules/{rule_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(address: Annotated[RulePath, Depends()], actor: ActorDep, rules: FromDishka[RecipeRules]) -> None:
    """Remove a rule, so the pages it matched fall to the rules after it, or to the active recipe.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the rule.
    :type address: RulePath
    :param actor: The signed-in account.
    :type actor: Actor
    :param rules: Rules service of the request.
    :type rules: RecipeRules
    """
    await rules.remove(actor, address.project_id, address.stage, address.rule_id)
