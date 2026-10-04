"""The manual edits of a page: a frame, an angle, a split line or a mask that a processor reads as an input.

An edit is saved for one step of a recipe on one stage of one page, replacing the one it had, and is sent as a form
because it may come with a mask the user painted. Saving or deleting an edit marks the stage of the page stale and
processes nothing. The shape is checked against the editor of the processor before the route runs.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Form, Path, Request, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.edits import EditForm, PageEditSchema
from bookreviver.api.schemas.page_history import PageStepChangeSchema
from bookreviver.api.schemas.page_settings import CarryForm, CarryOverSchema
from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, ProjectId, StepId
from bookreviver.domain.values import PageStepKey, Slice
from bookreviver.services.edits import EditService
from bookreviver.services.page_carry import CarryOverService

router = APIRouter(prefix='/projects', tags=['edits'], route_class=DishkaRoute)

# The form may hold a file, so the schema says it is a multipart form, and the generated client sends it as one
MULTIPART_FORM: str = 'multipart/form-data'


@dataclass(frozen=True)
class EditPath:
    """The identifiers in the address of the edits of one stage of one page.

    :ivar project_id: Identifier of the project.
    :ivar page_id: Identifier of the page.
    :ivar stage: The stage.
    """

    project_id: Annotated[ProjectId, Path(description='Identifier of the project')]
    page_id: Annotated[PageId, Path(description='Identifier of the page')]
    stage: Annotated[Stage, Path(description='Stage of the step that reads the edit')]


@dataclass(frozen=True)
class StepEditPath(EditPath):
    """The identifiers in the address of the edit one step of a recipe reads.

    :ivar step_id: Identifier of the step.
    """

    step_id: Annotated[StepId, Path(description='Identifier of the step of a recipe that reads the edit')]

    @property
    def key(self) -> PageStepKey:
        """The key the edit is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)


@router.get('/{project_id}/pages/{page_id}/edits/{stage}')
async def list_edits(
    address: Annotated[EditPath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    request: Request,
    edits: FromDishka[EditService],
) -> Page[PageEditSchema]:
    """List the manual edits of one stage of a page, by processor.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the stage.
    :type address: EditPath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves a mask.
    :type request: Request
    :param edits: Edit service of the request.
    :type edits: EditService
    :returns: One page of the edits.
    :rtype: Page[PageEditSchema]
    """

    def to_schema(edit: PageEdit) -> PageEditSchema:
        """Build the schema of one edit.

        :param edit: The edit.
        :type edit: PageEdit
        :returns: The schema.
        :rtype: PageEditSchema
        """
        return PageEditSchema.of(edit, request)

    pager = Pager[PageEdit, PageEditSchema](params, to_schema)
    found = await edits.list(actor, address.project_id, address.page_id, address.stage)
    window = found[pager.request.offset : pager.request.offset + pager.request.limit]
    return pager.page(Slice(items=window, total=len(found)))


@router.put('/{project_id}/pages/{page_id}/edits/{stage}/{step_id}')
async def put_edit(
    address: Annotated[StepEditPath, Depends()],
    form: Annotated[EditForm, Form(media_type=MULTIPART_FORM)],
    actor: ActorDep,
    request: Request,
    edits: FromDishka[EditService],
) -> PageEditSchema:
    """Save the edit a step of a recipe reads, replacing the one it had, and mark the stage of the page stale.

    The form carries the editor that drew the edit, its shape as JSON text, and the mask as a file for a brush edit. The
    answer is 422 for an edit the processor of the step does not read, and nothing is processed by this request.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page, the stage and the step.
    :type address: StepEditPath
    :param form: The editor, the shape and the mask.
    :type form: EditForm
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves a mask.
    :type request: Request
    :param edits: Edit service of the request.
    :type edits: EditService
    :returns: The edit as stored, with its hash.
    :rtype: PageEditSchema
    """
    stored = await edits.save(actor, address.project_id, address.key, form.to_edit(), form.mask)
    return PageEditSchema.of(stored, request)


@router.delete('/{project_id}/pages/{page_id}/edits/{stage}/{step_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_edit(
    address: Annotated[StepEditPath, Depends()], actor: ActorDep, edits: FromDishka[EditService]
) -> None:
    """Delete the edit a step of a recipe reads, and mark the stage of the page stale.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page, the stage and the step.
    :type address: StepEditPath
    :param actor: The signed-in account.
    :type actor: Actor
    :param edits: Edit service of the request.
    :type edits: EditService
    """
    await edits.delete(actor, address.project_id, address.key)


@router.post('/{project_id}/pages/{page_id}/edits/{stage}/{step_id}/carry-over')
async def carry_over_edit(
    address: Annotated[StepEditPath, Depends()],
    form: CarryForm,
    actor: ActorDep,
    carry: FromDishka[CarryOverService],
) -> CarryOverSchema:
    """Carry the shape this page has set by hand for a step over to other pages, as one batch, and mark them stale.

    The pages are the following ones, the selected ones, or every page of the condition of the step. The whole shape is
    carried and not a part of it, and the settings of each page stay as they are. A page that has a shape of its own set
    by hand is left as it is and listed as skipped, unless the form asks to write over it, and a page that has the same
    shape is neither written nor listed. The changes share a batch, so one undo takes the shape back from every page.
    The answer is 404 when this page has no shape set by hand for the step, and 422 for an edit that is a mask, which
    belongs to one page, and for a carry-over to the selected pages that names none.

    \N{FORM FEED}
    :param address: Identifiers of the project, the source page, the stage and the step.
    :type address: StepEditPath
    :param form: The pages to carry the shape to, and whether to write over a shape of their own.
    :type form: CarryForm
    :param actor: The signed-in account.
    :type actor: Actor
    :param carry: Carry-over service of the request.
    :type carry: CarryOverService
    :returns: The batch, the changes written and the pages skipped.
    :rtype: CarryOverSchema
    """
    carried = await carry.carry(actor, address.project_id, form.to_request(address.key))
    return CarryOverSchema(
        batch_id=carried.batch_id,
        changes=[PageStepChangeSchema.model_validate(change) for change in carried.changes],
        skipped=list(carried.skipped),
    )
