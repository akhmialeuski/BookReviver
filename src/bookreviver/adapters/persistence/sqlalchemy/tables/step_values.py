"""The tables of the values of a step for the odd pages, the even pages and the pages of a group."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.adapters.persistence.sqlalchemy.columns import enum_by_value
from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    EMPTY_OBJECT,
    GROUP_ID_COLUMN,
    PARITY_SCOPE,
    STAGE_COLUMN,
    BookTable,
)
from bookreviver.adapters.persistence.sqlalchemy.tables.pages import PageGroupRow
from bookreviver.domain.stages import Stage
from bookreviver.domain.step_values import ValueScope


class StepValuesRow(BookTable):
    """Row of the values of one step for the odd pages or the even pages.

    The step is named by the identifier its recipe gives it, with no foreign key, as in ``page_step_states``. The values
    of a group are rows of ``group_values``, whose key names the group in place of the scope.

    :ivar step_id: Identifier of the step.
    :ivar scope: The odd pages or the even pages, stored by value.
    :ivar stage: Stage of the step, stored by value.
    :ivar params: The fields of the parameters of the step that the pages change, as a JSON object.
    :ivar updated_at: Time the values were last saved.
    """

    __tablename__ = 'step_values'
    __table_args__ = (Index(None, STAGE_COLUMN), CheckConstraint(PARITY_SCOPE, name='parity_scope'))

    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[ValueScope] = mapped_column(enum_by_value(ValueScope), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, server_default=EMPTY_OBJECT)
    updated_at: Mapped[datetime]


class GroupValuesRow(BookTable):
    """Row of the values of one step for the pages of one group.

    The step is named by its identifier with no foreign key, as in ``step_values``.

    :ivar step_id: Identifier of the step.
    :ivar group_id: The group the values are for.
    :ivar stage: Stage of the step, stored by value.
    :ivar params: The fields of the parameters of the step that the pages of the group change, as a JSON object.
    :ivar updated_at: Time the values were last saved.
    """

    __tablename__ = 'group_values'
    __table_args__ = (Index(None, STAGE_COLUMN), Index(None, GROUP_ID_COLUMN))

    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey(PageGroupRow.id), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, server_default=EMPTY_OBJECT)
    updated_at: Mapped[datetime]
