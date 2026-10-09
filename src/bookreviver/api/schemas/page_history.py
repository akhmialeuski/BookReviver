"""Schemas of the history of a step on a page: a change, what an undo wrote and what a clear deleted."""

from datetime import datetime
from typing import Any

from pydantic import Field

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.domain.enums import ChangeSource, Stage, StepLayer, ValueScope
from bookreviver.domain.ids import ChangeBatchId, PageId, PageStepChangeId, StepId


class UndoForm(RequestModel):
    """What an undo takes back.

    :ivar change_id: The oldest change to take back, which takes back every change after it as well, or omitted for the
                     newest change alone.
    """

    change_id: PageStepChangeId | None = Field(
        default=None, description='The oldest change to take back, with every later one, or none for the newest change'
    )


class PageStepChangeSchema(ResponseModel):
    """One change of a layer of a step on a page.

    :ivar id: Identifier of the change.
    :ivar page_id: Page the change was made on.
    :ivar stage: Stage of the step.
    :ivar step_id: Identifier of the step.
    :ivar layer: The layer that changed.
    :ivar scope: Whose settings the layer holds: the page's own, or the odd pages, the even pages or a group it takes.
    :ivar group_label: Label of the group for the scope of a group, and empty for the others.
    :ivar before: Content of the layer before the change, or None when it was empty.
    :ivar after: Content of the layer after the change, or None when the change emptied it.
    :ivar source: What made the change.
    :ivar batch_id: Identifier shared by the changes of one batch, which are undone together, or None.
    :ivar undoes: The change this one takes back, or None for a change that is not an undo.
    :ivar undone: Whether a later change took this one back, so it no longer stands.
    :ivar created_at: When the change was made.
    :ivar sequence: Place of the change in the history of its page.
    """

    id: PageStepChangeId
    page_id: PageId
    stage: Stage
    step_id: StepId
    layer: StepLayer
    scope: ValueScope
    group_label: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    source: ChangeSource
    batch_id: ChangeBatchId | None
    undoes: PageStepChangeId | None
    undone: bool = False
    created_at: datetime
    sequence: int


class UndoneSchema(ResponseModel):
    """What an undo wrote.

    :ivar changes: The undos that were written, which are none when the step had no change to take back.
    """

    changes: list[PageStepChangeSchema]


class ClearedSchema(ResponseModel):
    """What clearing a step on a page deleted.

    :ivar changes: How many changes of the history were deleted, which is none when the step had no history on the page.
    :ivar versions: How many versions were deleted: the results of the step on the page and the results that read them.
    """

    changes: int
    versions: int
