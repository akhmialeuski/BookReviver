"""Schemas of the settings a page has for a step: the value a request sets, the settings as stored, and the batches."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, model_validator

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.api.schemas.page_history import PageStepChangeSchema
from bookreviver.api.schemas.types import PageIdList
from bookreviver.domain.enums import CarryScope, ResetScope, Stage, StepLayer
from bookreviver.domain.ids import ChangeBatchId, PageId, StepId
from bookreviver.domain.values import RESET_NEEDS_PAGE, RESET_NEEDS_STEP, CarryRequest, ResetRequest
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


class ResetBody(RequestModel):
    """What a reset of steps to their defaults goes over; the stage is in the address.

    :ivar scope: The step on the open page, every step of the stage on the open page, the step on every page, or every
                 step of the stage on every page.
    :ivar page_id: The open page, which the scopes of one page need and the others ignore.
    :ivar step_id: The step, which the scopes of one step need and the others ignore.
    :ivar confirm: Confirmation that a reset that reaches other pages takes the settings and the edits of the pages that
                   have any, without which such a reset is refused with 409. A reset of the open page needs none.
    """

    scope: ResetScope
    page_id: PageId | None = None
    step_id: StepId | None = None
    confirm: bool = False

    @model_validator(mode='after')
    def _scope_has_what_it_needs(self) -> Self:
        """Check that the scope has the page and the step it needs.

        :returns: The body unchanged.
        :rtype: Self
        :raises ValueError: If a scope of one page names no page, or a scope of one step names no step.
        """
        if self.scope in {ResetScope.PAGE_STEP, ResetScope.PAGE} and self.page_id is None:
            raise ValueError(RESET_NEEDS_PAGE)
        if self.scope in {ResetScope.PAGE_STEP, ResetScope.STEP} and self.step_id is None:
            raise ValueError(RESET_NEEDS_STEP)
        return self

    def to_request(self, stage: Stage) -> ResetRequest:
        """Return the reset as the domain states it.

        :param stage: The stage from the address.
        :type stage: Stage
        :returns: The request.
        :rtype: ResetRequest
        """
        return ResetRequest(
            stage=stage, scope=self.scope, page_id=self.page_id, step_id=self.step_id, confirm=self.confirm
        )


class ResetImpactSchema(ResponseModel):
    """How many pages a reset would take work from.

    :ivar scope: The scope of the reset.
    :ivar hand_pages: How many pages have a manual edit on a step the reset goes over.
    :ivar settings_pages: How many pages change at least one field of a step the reset goes over.
    :ivar affected: How many pages lose work, a page with both counted once.
    """

    scope: ResetScope
    hand_pages: int
    settings_pages: int
    affected: int


class StepResetSchema(ResponseModel):
    """What a reset did, which is one batch of the history.

    :ivar batch_id: The batch the changes share, which an undo of any of them takes back as a whole.
    :ivar changes: The changes written, one for each layer a page lost.
    """

    batch_id: ChangeBatchId
    changes: list[PageStepChangeSchema]
