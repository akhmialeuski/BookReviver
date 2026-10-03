"""The manual edits of a page: a frame, an angle, a split line or a mask that a processor reads as an input.

An edit is saved for one processor on one stage of one page, replacing the one it had, and is sent as a form because it
may come with a mask the user painted. Saving or deleting an edit marks the stage of the page stale and processes
nothing. The shape is checked against the editor of the processor before the route runs.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Form, Path, Request, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.edits import EditForm, PageEditSchema
from bookreviver.api.schemas.types import ProcessorKeyText
from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.domain.values import PageEditKey, Slice
from bookreviver.services.edits import EditService

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
    stage: Annotated[Stage, Path(description='Stage of the processor that reads the edit')]


@dataclass(frozen=True)
class ProcessorEditPath(EditPath):
    """The identifiers in the address of the edit one processor reads.

    :ivar processor_key: Key of the processor.
    """

    processor_key: Annotated[ProcessorKeyText, Path(description='Key of the processor that reads the edit')]

    @property
    def key(self) -> PageEditKey:
        """The key the edit is stored under."""
        return PageEditKey(self.page_id, self.stage, self.processor_key)


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


@router.put('/{project_id}/pages/{page_id}/edits/{stage}/{processor_key}')
async def put_edit(
    address: Annotated[ProcessorEditPath, Depends()],
    form: Annotated[EditForm, Form(media_type=MULTIPART_FORM)],
    actor: ActorDep,
    request: Request,
    edits: FromDishka[EditService],
) -> PageEditSchema:
    """Save the edit a processor reads, replacing the one it had, and mark the stage of the page stale.

    The form carries the editor that drew the edit, its shape as JSON text, and the mask as a file for a brush edit. The
    answer is 422 for an edit the processor does not read, and nothing is processed by this request.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page, the stage and the processor.
    :type address: ProcessorEditPath
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


@router.delete('/{project_id}/pages/{page_id}/edits/{stage}/{processor_key}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_edit(
    address: Annotated[ProcessorEditPath, Depends()], actor: ActorDep, edits: FromDishka[EditService]
) -> None:
    """Delete the edit a processor reads, and mark the stage of the page stale.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page, the stage and the processor.
    :type address: ProcessorEditPath
    :param actor: The signed-in account.
    :type actor: Actor
    :param edits: Edit service of the request.
    :type edits: EditService
    """
    await edits.delete(actor, address.project_id, address.key)
