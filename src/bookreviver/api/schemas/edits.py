"""Schemas of the manual edits of a page: the form an edit is sent in, and the edit as it is stored.

An edit is sent as a form, because it may come with a mask the user painted: the editor that drew it, the shape as JSON
text, and the mask as a file. FastAPI checks the form with a model, and the shape is read into the domain shape of the
editor by ``geometry_from_data``, so a shape that does not fit its editor is a 422 before the route runs.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from fastapi import UploadFile
from pydantic import Json, model_validator

from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.domain.enums import EditorKind, Stage
from bookreviver.domain.geometry import geometry_from_data
from bookreviver.domain.ids import PageId, StepId
from bookreviver.domain.values import NewPageEdit

if TYPE_CHECKING:
    from starlette.requests import Request

    from bookreviver.domain.entities import PageEdit

SHAPE_DOES_NOT_FIT: str = 'The shape does not fit the {editor} editor: {reason}.'


class EditForm(RequestModel):
    """A manual edit as the form of a request carries it.

    :ivar kind: Editor that drew the edit.
    :ivar geometry: The shape the user drew as JSON, or omitted for an edit that is only a mask.
    :ivar mask: The mask the user painted, or omitted.
    """

    kind: EditorKind
    geometry: Json[dict[str, Any]] | None = None
    mask: UploadFile | None = None

    @model_validator(mode='after')
    def _shape_fits_its_editor(self) -> Self:
        """Check that the shape is the one the editor draws.

        :returns: The form unchanged.
        :rtype: Self
        :raises ValueError: If the shape does not fit the editor.
        """
        self.to_edit()
        return self

    def to_edit(self) -> NewPageEdit:
        """Return the edit as the domain states it.

        :returns: The edit with its shape read into the shape of its editor.
        :rtype: NewPageEdit
        :raises ValueError: If the shape does not fit the editor.
        """
        if self.geometry is None:
            return NewPageEdit(kind=self.kind)
        try:
            shape = geometry_from_data(self.kind, self.geometry)
        except (KeyError, TypeError, ValueError) as error:
            err_msg = SHAPE_DOES_NOT_FIT.format(editor=self.kind.label.lower(), reason=error)
            raise ValueError(err_msg) from error
        return NewPageEdit(kind=self.kind, geometry=shape)


class PageEditSchema(ResponseModel):
    """A manual edit of a page.

    :ivar page_id: Page the edit belongs to.
    :ivar stage: Stage of the step that reads the edit.
    :ivar step_id: Identifier of that step.
    :ivar kind: Editor that drew the edit.
    :ivar geometry: The shape as JSON, or None for an edit that is only a mask.
    :ivar mask: Path of the mask on the IIIF route, or None.
    :ivar edit_hash: Hash of the shape and the mask.
    :ivar updated_at: When the edit was last saved.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId
    kind: EditorKind
    geometry: dict[str, Any] | None
    mask: str | None
    edit_hash: str
    updated_at: datetime

    @classmethod
    def of(cls, edit: PageEdit, request: Request) -> Self:
        """Build the schema of an edit.

        :param edit: The edit.
        :type edit: PageEdit
        :param request: The request, whose application knows the route that serves the mask.
        :type request: Request
        :returns: The schema.
        :rtype: Self
        """
        mask = None if edit.mask_key is None else str(request.app.url_path_for(RouteName.IIIF_FILE, key=edit.mask_key))
        return cls(
            page_id=edit.page_id,
            stage=edit.stage,
            step_id=edit.step_id,
            kind=edit.kind,
            geometry=None if edit.geometry is None else edit.geometry.to_data(),
            mask=mask,
            edit_hash=edit.edit_hash,
            updated_at=edit.updated_at,
        )
