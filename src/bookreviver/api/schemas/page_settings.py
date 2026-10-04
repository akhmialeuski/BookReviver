"""Schemas of the settings a page has for a step: the value a request sets, the settings as stored, and a carry-over."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, model_validator

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.page_history import PageStepChangeSchema
from bookreviver.api.schemas.types import PageIdList
from bookreviver.domain.enums import CarryScope, Stage, StepLayer
from bookreviver.domain.ids import ChangeBatchId, PageId, StepId
from bookreviver.domain.values import CarryRequest
from bookreviver.services.page_carry import SELECTED_NEEDS_PAGES

if TYPE_CHECKING:
    from bookreviver.domain.values import PageStepKey


class PageSettingForm(RequestModel):
    """The value one page uses for one field of the parameters of a step.

    :ivar value: The value, as JSON, which the processor of the step checks against the field.
    """

    value: Any = Field(description='The value the page uses for the field')


class PageStepSettingsSchema(ResponseModel):
    """The fields of the parameters of a step that one page changes.

    :ivar page_id: Page the settings belong to.
    :ivar stage: Stage of the step.
    :ivar step_id: Identifier of the step.
    :ivar params: The fields the page changes, by name, each with the value the page uses. The others come from the
                  step of the recipe.
    :ivar updated_at: When the settings were last saved.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId
    params: dict[str, Any]
    updated_at: datetime


class CarryForm(RequestModel):
    """The pages a setting of one page is carried over to.

    :ivar scope: The following pages, the selected pages, or every page of the condition of the step.
    :ivar page_ids: The selected pages, which the scope of the selected pages needs and the other scopes ignore.
    :ivar overwrite: Whether a page that has another value of its own for the field takes the value as well, instead of
                     being skipped.
    """

    scope: CarryScope
    page_ids: PageIdList | None = None
    overwrite: bool = False

    @model_validator(mode='after')
    def _selected_names_pages(self) -> Self:
        """Check that a carry-over to the selected pages names them.

        :returns: The form unchanged.
        :rtype: Self
        :raises ValueError: If the scope is the selected pages and no page is named.
        """
        if self.scope is CarryScope.SELECTED and self.page_ids is None:
            raise ValueError(SELECTED_NEEDS_PAGES)
        return self

    def to_request(self, key: PageStepKey, name: str | None = None) -> CarryRequest:
        """Return the carry-over as the domain states it.

        :param key: The source page, the stage and the step from the address.
        :type key: PageStepKey
        :param name: Name of the field from the address, or None to carry the shape set by hand.
        :type name: str | None
        :returns: The request.
        :rtype: CarryRequest
        """
        return CarryRequest(
            key=key,
            layer=StepLayer.SETTINGS if name is not None else StepLayer.HAND,
            name=name,
            scope=self.scope,
            page_ids=() if self.page_ids is None else tuple(self.page_ids),
            overwrite=self.overwrite,
        )


class CarryOverSchema(ResponseModel):
    """What a carry-over did, which is one batch of the history.

    :ivar batch_id: The batch the changes share, which an undo of any of them takes back as a whole.
    :ivar changes: The changes written, one on each page that took the value.
    :ivar skipped: The pages left as they were because they have a value of their own for the field.
    """

    batch_id: ChangeBatchId
    changes: list[PageStepChangeSchema]
    skipped: list[PageId]
