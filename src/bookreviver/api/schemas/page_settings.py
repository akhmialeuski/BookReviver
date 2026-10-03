"""Schemas of the settings a page has for a step: the value a request sets, and the settings of a step as stored."""

from datetime import datetime
from typing import Any

from pydantic import Field

from bookreviver.api.schemas.base import RequestModel, ResponseModel
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, StepId


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
