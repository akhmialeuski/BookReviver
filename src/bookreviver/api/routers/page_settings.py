"""The values a setting of a step has for a part of the pages, set and taken back one field at a time.

The part is the open page, the pages the user selected, the odd pages, the even pages, or a group. Setting or taking
back a value marks the stage of each page whose parameters change stale, and processes nothing, and the value is checked
against the parameters of the processor of the step on each page it reaches before the route answers, so a field the
processor does not have, or a value out of its range, is a 422. A page takes each field from its own value, else from
the value of its group, else from the value of the odd or the even pages, else from the step of the recipe. The changes
are one batch of the history, so one undo takes the value back from every page it reached.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Query
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import StagePath
from bookreviver.api.schemas.page_history import PageStepChangeSchema
from bookreviver.api.schemas.page_settings import (
    PageStepSettingsSchema,
    ValueChangesSchema,
    ValueForm,
    ValueTargetModel,
)
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, ProjectId, StepId
from bookreviver.domain.step_values import PageStepSettings, ValueField
from bookreviver.domain.values import Slice
from bookreviver.services.page_settings import PageSettingsService

if TYPE_CHECKING:
    from bookreviver.domain.step_values import ValueTarget

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
    stage: Annotated[Stage, Path(description='Stage of the steps the settings are for')]


@dataclass(frozen=True)
class FieldPath(StagePath):
    """The identifiers in the address of one field of one step of a recipe.

    :ivar step_id: Identifier of the step.
    :ivar name: Name of the field in the parameters of the step.
    """

    step_id: Annotated[StepId, Path(description='Identifier of the step of a recipe')]
    name: Annotated[str, Path(description='Name of the field in the parameters of the step', min_length=1)]

    def of(self, target: ValueTarget) -> ValueField:
        """Name the field together with the pages a value of it is for.

        :param target: The pages the value is for.
        :type target: ValueTarget
        :returns: The field the service works on.
        :rtype: ValueField
        """
        return ValueField(stage=self.stage, step_id=self.step_id, name=self.name, target=target)


@router.get('/{project_id}/pages/{page_id}/settings/{stage}')
async def list_settings(
    address: Annotated[SettingsPath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    settings: FromDishka[PageSettingsService],
) -> Page[PageStepSettingsSchema]:
    """List the steps of one stage of a page that have values of the page or of a part of the pages.

    Each step comes with the fields the page changes for itself, with the values of the odd pages, the even pages and
    the groups for the step, and with the parameters the step runs with on the page.

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

    def to_schema(entry: PageStepSettings) -> PageStepSettingsSchema:
        """Build the schema of the settings of one step.

        :param entry: What the page runs the step with.
        :type entry: PageStepSettings
        :returns: The schema.
        :rtype: PageStepSettingsSchema
        """
        return PageStepSettingsSchema.model_validate(entry)

    pager = Pager[PageStepSettings, PageStepSettingsSchema](params, to_schema)
    found = await settings.list(actor, address.project_id, address.page_id, address.stage)
    window = found[pager.request.offset : pager.request.offset + pager.request.limit]
    return pager.page(Slice(items=window, total=len(found)))


@router.put('/{project_id}/stages/{stage}/steps/{step_id}/values/{name}')
async def put_value(
    address: Annotated[FieldPath, Depends()],
    form: ValueForm,
    actor: ActorDep,
    settings: FromDishka[PageSettingsService],
) -> ValueChangesSchema:
    """Set the value pages use for one field of a step, and mark the stage of each page whose parameters change stale.

    The pages are the open page or the pages the user selected, the odd pages, the even pages, or the pages of a group.
    The other pages keep what they had, and so do these pages for every other field. A page that takes the field from
    a stronger part, which is its own value or its group, is left as it is. The answer is 422 for a field the processor
    of the step does not have and for a value out of its range, and 404 for a step no recipe of the stage has, and
    nothing is processed by this request.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage, the step and the field.
    :type address: FieldPath
    :param form: The pages and the value.
    :type form: ValueForm
    :param actor: The signed-in account.
    :type actor: Actor
    :param settings: Page settings service of the request.
    :type settings: PageSettingsService
    :returns: The batch and the changes written, one on each page whose parameters change.
    :rtype: ValueChangesSchema
    """
    done = await settings.change(actor, address.project_id, address.of(form.to_target()), form.value)
    return ValueChangesSchema(
        batch_id=done.batch_id, changes=[PageStepChangeSchema.model_validate(change) for change in done.changes]
    )


@router.delete('/{project_id}/stages/{stage}/steps/{step_id}/values/{name}')
async def delete_value(
    address: Annotated[FieldPath, Depends()],
    target: Annotated[ValueTargetModel, Query()],
    actor: ActorDep,
    settings: FromDishka[PageSettingsService],
) -> ValueChangesSchema:
    """Take a field back from the pages, so they take it from the next part, and mark their stages stale.

    The answer is 404 when none of the pages has a value of its own for the field, or the step is not one of the stage.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage, the step and the field.
    :type address: FieldPath
    :param target: The pages, the odd pages, the even pages or the group the value was set for.
    :type target: ValueTargetModel
    :param actor: The signed-in account.
    :type actor: Actor
    :param settings: Page settings service of the request.
    :type settings: PageSettingsService
    :returns: The batch and the changes written, one on each page whose parameters change.
    :rtype: ValueChangesSchema
    """
    done = await settings.reset(actor, address.project_id, address.of(target.to_target()))
    return ValueChangesSchema(
        batch_id=done.batch_id, changes=[PageStepChangeSchema.model_validate(change) for change in done.changes]
    )
