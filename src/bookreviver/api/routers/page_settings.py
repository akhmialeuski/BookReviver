"""The settings a page has for a step of a recipe: one field of its parameters that only this page uses.

A field is set or taken back one at a time. Setting or taking back a field marks the stage of the page stale and
processes nothing, and the value is checked against the parameters of the processor of the step before the route
answers, so a field the processor does not have, or a value out of its range, is a 422. The value a page has for a
field may be carried over to other pages in one batch, which one undo takes back from every page. The settings and the
manual edits of the steps of a stage are taken away from one page or from every page by a reset, which is one batch as
well.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import StagePath
from bookreviver.api.schemas.page_history import PageStepChangeSchema
from bookreviver.api.schemas.page_settings import (
    CarryForm,
    CarryOverSchema,
    PageSettingForm,
    PageStepSettingsSchema,
    ResetBody,
    ResetImpactSchema,
    StepResetSchema,
)
from bookreviver.domain.entities import PageStepState
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, ProjectId, StepId
from bookreviver.domain.values import PageStepKey, Slice
from bookreviver.services.page_carry import CarryOverService
from bookreviver.services.page_settings import PageSettingsService
from bookreviver.services.step_resets import StepResetService

router = APIRouter(prefix='/projects', tags=['page-settings'], route_class=DishkaRoute)


@dataclass(frozen=True)
class SettingsPath:
    """The identifiers in the address of the settings of one stage of one page.

    :ivar project_id: Identifier of the project.
    :ivar page_id: Identifier of the page.
    :ivar stage: The stage.
    """

    project_id: Annotated[ProjectId, Path(description='Identifier of the project')]
    page_id: Annotated[PageId, Path(description='Identifier of the page')]
    stage: Annotated[Stage, Path(description='Stage of the step the settings are for')]


@dataclass(frozen=True)
class FieldPath(SettingsPath):
    """The identifiers in the address of one field of one step of a recipe on one page.

    :ivar step_id: Identifier of the step.
    :ivar name: Name of the field in the parameters of the step.
    """

    step_id: Annotated[StepId, Path(description='Identifier of the step of a recipe')]
    name: Annotated[str, Path(description='Name of the field in the parameters of the step', min_length=1)]

    @property
    def key(self) -> PageStepKey:
        """The key the state of the step on the page is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)


@router.get('/{project_id}/pages/{page_id}/settings/{stage}')
async def list_settings(
    address: Annotated[SettingsPath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    settings: FromDishka[PageSettingsService],
) -> Page[PageStepSettingsSchema]:
    """List the steps of one stage of a page that have settings of the page, with the fields the page changes.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the stage.
    :type address: SettingsPath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param settings: Page settings service of the request.
    :type settings: PageSettingsService
    :returns: One page of the settings, by step.
    :rtype: Page[PageStepSettingsSchema]
    """

    def to_schema(state: PageStepState) -> PageStepSettingsSchema:
        """Build the schema of the settings of one step.

        :param state: The state of the step on the page.
        :type state: PageStepState
        :returns: The schema.
        :rtype: PageStepSettingsSchema
        """
        return PageStepSettingsSchema.model_validate(state)

    pager = Pager[PageStepState, PageStepSettingsSchema](params, to_schema)
    found = await settings.list(actor, address.project_id, address.page_id, address.stage)
    window = found[pager.request.offset : pager.request.offset + pager.request.limit]
    return pager.page(Slice(items=window, total=len(found)))


@router.put('/{project_id}/pages/{page_id}/settings/{stage}/{step_id}/{name}')
async def put_setting(
    address: Annotated[FieldPath, Depends()],
    form: PageSettingForm,
    actor: ActorDep,
    settings: FromDishka[PageSettingsService],
) -> PageStepSettingsSchema:
    """Set the value one page uses for one field of a step, and mark the stage of the page stale.

    The other pages keep the value of the step of the recipe, and so does this page for every other field. The answer is
    422 for a field the processor of the step does not have and for a value out of its range, and nothing is processed
    by this request.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page, the stage, the step and the field.
    :type address: FieldPath
    :param form: The value.
    :type form: PageSettingForm
    :param actor: The signed-in account.
    :type actor: Actor
    :param settings: Page settings service of the request.
    :type settings: PageSettingsService
    :returns: The settings of the step on the page, with the field set.
    :rtype: PageStepSettingsSchema
    """
    state = await settings.change(actor, address.project_id, address.key, address.name, form.value)
    return PageStepSettingsSchema.model_validate(state)


@router.delete(
    '/{project_id}/pages/{page_id}/settings/{stage}/{step_id}/{name}', status_code=status.HTTP_204_NO_CONTENT
)
async def delete_setting(
    address: Annotated[FieldPath, Depends()], actor: ActorDep, settings: FromDishka[PageSettingsService]
) -> None:
    """Take a field back from the page, so it runs with the value of the step of the recipe, and mark the stage stale.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page, the stage, the step and the field.
    :type address: FieldPath
    :param actor: The signed-in account.
    :type actor: Actor
    :param settings: Page settings service of the request.
    :type settings: PageSettingsService
    """
    await settings.reset(actor, address.project_id, address.key, address.name)


@router.post('/{project_id}/pages/{page_id}/settings/{stage}/{step_id}/{name}/carry-over')
async def carry_over_setting(
    address: Annotated[FieldPath, Depends()],
    form: CarryForm,
    actor: ActorDep,
    carry: FromDishka[CarryOverService],
) -> CarryOverSchema:
    """Carry the value this page has for a field of a step over to other pages, as one batch, and mark them stale.

    The pages are the following ones, the selected ones, or every page of the condition of the step. A page that has a
    value of its own for the field is left as it is and listed as skipped, unless the form asks to write over it. Each
    page that takes the value changes this one field and no other, and the changes share a batch, so one undo takes the
    value back from every page. The answer is 404 when this page does not change the field, and 422 for a carry-over to
    the selected pages that names none, or a value out of range for another field of a page.

    \N{FORM FEED}
    :param address: Identifiers of the project, the source page, the stage, the step and the field.
    :type address: FieldPath
    :param form: The pages to carry the value to, and whether to write over a value of their own.
    :type form: CarryForm
    :param actor: The signed-in account.
    :type actor: Actor
    :param carry: Carry-over service of the request.
    :type carry: CarryOverService
    :returns: The batch, the changes written and the pages skipped.
    :rtype: CarryOverSchema
    """
    carried = await carry.carry(actor, address.project_id, form.to_request(address.key, address.name))
    return CarryOverSchema(
        batch_id=carried.batch_id,
        changes=[PageStepChangeSchema.model_validate(change) for change in carried.changes],
        skipped=list(carried.skipped),
    )


@router.post('/{project_id}/stages/{stage}/reset')
async def reset_steps(
    address: Annotated[StagePath, Depends()],
    body: ResetBody,
    actor: ActorDep,
    resets: FromDishka[StepResetService],
) -> StepResetSchema:
    """Take the settings and the manual edits of steps away from one page or from every page, and mark the stages stale.

    The scope is the step on the open page, every step of the stage on the open page, the step on every page, or every
    step of the stage on every page. The pages use the values of the recipe again, which stays as it is, and the next
    run of the stage finds the shapes anew. The changes of the history share a batch from a reset, so one undo gives
    the work back on every page. A reset that reaches other pages and takes work from some of them answers 409 until the
    body confirms it. The answer is 404 for a step that no recipe of the stage has, and 422 for a scope without the page
    or the step it needs.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The scope, the page and the step it needs, and the confirmation.
    :type body: ResetBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param resets: Step reset service of the request.
    :type resets: StepResetService
    :returns: The batch and the changes written.
    :rtype: StepResetSchema
    """
    done = await resets.reset(actor, address.project_id, body.to_request(address.stage))
    return StepResetSchema(
        batch_id=done.batch_id, changes=[PageStepChangeSchema.model_validate(c) for c in done.changes]
    )


@router.post('/{project_id}/stages/{stage}/reset-impact')
async def reset_impact(
    address: Annotated[StagePath, Depends()],
    body: ResetBody,
    actor: ActorDep,
    resets: FromDishka[StepResetService],
) -> ResetImpactSchema:
    """Count the pages a reset would take work from, so the user can confirm it before it is sent.

    The body is the one of the reset. Nothing is written.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The reset.
    :type body: ResetBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param resets: Step reset service of the request.
    :type resets: StepResetService
    :returns: The pages that have an edit, the pages that have settings, and the pages that lose work.
    :rtype: ResetImpactSchema
    """
    counted = await resets.impact(actor, address.project_id, body.to_request(address.stage))
    return ResetImpactSchema.model_validate(counted)
