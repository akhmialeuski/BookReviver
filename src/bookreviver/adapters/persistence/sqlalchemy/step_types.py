"""The active type of each step of a book, which tells the rows a page keeps for a step that a run reads.

A step has one type at a time, the processor ``steps.type_key`` names. What the pages keep for a step, their settings
and edits in ``page_step_states``, the values of their parts in ``step_values`` and ``group_values``, and the changes
of their history in ``step_changes``, is kept for the type the step had when it was written, so a step whose type is
replaced keeps the rows of the earlier type and finds them again when that type comes back. The repositories read and
write the rows of the active type alone, through :class:`StepTypes`, so a service asks for the state of a step on a page
and is given the one of its type, and no service names a type to filter by.
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from bookreviver.adapters.persistence.sqlalchemy.tables import StepRow
from bookreviver.domain.errors import NotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.tables import (
        GroupValuesRow,
        PageStepStateRow,
        StepChangeRow,
        StepValuesRow,
    )


class StepTypes:
    """The active types of the steps of a book on the session of one transaction.

    The row of a step is read once and held, since the identity map of the session holds its rows weakly, and it is
    the row of the identity map, so a type replaced earlier in the transaction is the one the later reads and writes of
    the transaction take.

    :ivar _session: Session of the transaction.
    :ivar _rows: The rows of the steps read so far, by identifier.
    """

    def __init__(self, session: Session) -> None:
        """Read the types on ``session``.

        :param session: Session of the transaction.
        :type session: Session
        """
        self._session = session
        self._rows: dict[UUID, StepRow] = {}

    def of(self, step_id: UUID) -> str:
        """Return the active type of a step, which the row a page keeps for the step is written under.

        :param step_id: The step.
        :type step_id: UUID
        :returns: Key of the processor that is the type of the step.
        :rtype: str
        :raises NotFoundError: If the book has no such step.
        """
        if (row := self._rows.get(step_id)) is None:
            if (row := self._session.get(StepRow, step_id)) is None:
                raise NotFoundError(step_id)
            self._rows[step_id] = row
        return row.type_key

    @staticmethod
    def active(
        table: type[PageStepStateRow | StepValuesRow | GroupValuesRow | StepChangeRow],
    ) -> ColumnElement[bool]:
        """Give the condition that a row of a table that names a step and a type is of the active type of its step.

        The condition compares the type of the row with that of its step in a subquery correlated to the row, so it
        joins any statement over the table as a filter.

        :param table: The class of a table that names a step and a type.
        :type table: type[PageStepStateRow | StepValuesRow | GroupValuesRow | StepChangeRow]
        :returns: The condition, true for the rows of the active type.
        :rtype: ColumnElement[bool]
        """
        return table.type_key == select(StepRow.type_key).where(StepRow.id == table.step_id).scalar_subquery()
