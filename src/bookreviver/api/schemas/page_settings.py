"""Schemas of the values a setting of a step has for a part of the pages: what a request sets, and what is listed."""

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.page_history import PageStepChangeSchema
from bookreviver.api.schemas.types import GroupLabel, PageIdList
from bookreviver.domain.enums import ValueScope
from bookreviver.domain.ids import ChangeBatchId, StepId
from bookreviver.domain.step_values import ValueTarget


class ValueTargetModel(RequestModel):
    """The pages a value of a setting is for, which is what a request names besides the field.

    :ivar scope: The pages, the odd pages, the even pages, or a group.
    :ivar page_ids: The pages, for the scope of pages: the open page, or the pages the user selected.
    :ivar group_label: Label of the group, for the scope of a group.
    """

    scope: ValueScope
    page_ids: PageIdList | None = None
    group_label: GroupLabel = ''

    @model_validator(mode='after')
    def _names_what_the_scope_needs(self) -> ValueTargetModel:
        """Check that the request names the pages or the label its scope needs, and nothing else.

        :returns: The model unchanged.
        :rtype: Self
        :raises ValueError: If a scope of pages names none, or another scope names some, or a group has no label, or
                            another scope has one.
        """
        self.to_target()
        return self

    def to_target(self) -> ValueTarget:
        """Return the pages as the domain states them.

        :returns: The target.
        :rtype: ValueTarget
        :raises ValueError: If the scope has not got what it needs.
        """
        page_ids = () if self.page_ids is None else tuple(self.page_ids)
        return ValueTarget(scope=self.scope, page_ids=page_ids, group_label=self.group_label)


class ValueForm(ValueTargetModel):
    """The value the pages use for one field of the parameters of a step.

    :ivar value: The value, as JSON, which the processor of the step checks against the field.
    """

    value: Any = Field(description='The value the pages use for the field')


class StepValuesSchema(ResponseModel):
    """The fields of the parameters of a step that the odd pages, the even pages or a group change.

    :ivar scope: The odd pages, the even pages or a group.
    :ivar group_label: Label of the group for the scope of a group, and empty for the others.
    :ivar params: The fields the pages change, by name, each with the value they use.
    :ivar updated_at: When the values were last saved.
    """

    scope: ValueScope
    group_label: str
    params: dict[str, Any]
    updated_at: datetime


class PageStepSettingsSchema(ResponseModel):
    """What one page runs a step with, and the values it comes from.

    :ivar step_id: Identifier of the step.
    :ivar params: The fields the page changes for itself, by name, each with the value the page uses.
    :ivar parts: The values of the odd pages, the even pages and the groups for the step, whichever of them the page
                 is in.
    :ivar effective: The parameters the step runs with on the page: the recipe, then the odd or the even pages, the
                     group of the page, and the page itself, each field taken from the strongest part that has a value.
    :ivar updated_at: When the values of the page were last saved, or None for a page that changes no field itself.
    """

    step_id: StepId
    params: dict[str, Any]
    parts: list[StepValuesSchema]
    effective: dict[str, Any]
    updated_at: datetime | None


class ValueChangesSchema(ResponseModel):
    """What setting a value for a part of the pages, or taking it back, did, which is one batch of the history.

    :ivar batch_id: The batch the changes share, which an undo of any of them takes back as a whole.
    :ivar changes: The changes written, one on each page whose parameters change.
    """

    batch_id: ChangeBatchId
    changes: list[PageStepChangeSchema]
