"""The settings a page has for a step of a recipe: one field of its parameters that only this page uses.

A field is set or taken back one at a time. Setting or taking back a field marks the stage of the page stale and
processes nothing, and the value is checked against the parameters of the processor of the step before the route
answers, so a field the processor does not have, or a value out of its range, is a 422.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.page_settings import PageSettingForm, PageStepSettingsSchema
from bookreviver.domain.entities import PageStepState
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, ProjectId, StepId
from bookreviver.domain.values import PageStepKey, Slice
from bookreviver.services.page_settings import PageSettingsService

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
