"""Operations the revisions of both schemas share, written over Alembic's ``op``.

A revision keeps its own copy of the values it checks, as :class:`ValueCheck` rows, and never imports the enums of the
domain, which change after the revision is written. The rows that a check would refuse are looked for first, and a
revision that finds any stops with their count before it changes the schema, so a migration never fails half way on a
constraint.
"""

from collections import defaultdict
from typing import TYPE_CHECKING, Final

from alembic import op
from attrs import frozen
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

# What stands between two values of a check, and the quote SQL doubles inside a value
VALUE_SEPARATOR: Final = ', '
QUOTE: Final = "'"
# Batch mode rebuilds the table even when it has nothing else to change, which is how SQLite gets a new constraint
REBUILD: Final = 'always'


@frozen
class ValueCheck:
    """The check that a column of a table holds only the values of one enum, or nothing.

    :ivar table: Name of the table.
    :ivar name: Name of the check, as the naming convention gives it.
    :ivar column: Name of the column.
    :ivar values: The values the column may hold.
    """

    table: str
    name: str
    column: str
    values: tuple[str, ...]

    @property
    def condition(self) -> str:
        """The condition of the check, over the column of its own table."""
        quoted = VALUE_SEPARATOR.join(QUOTE + value.replace(QUOTE, QUOTE * 2) + QUOTE for value in self.values)
        return f'"{self.column}" IN ({quoted})'


def refuse_values_outside(checks: Sequence[ValueCheck]) -> None:
    """Stop the revision when a row holds a value one of the checks would refuse.

    :param checks: The checks the revision adds.
    :type checks: Sequence[ValueCheck]
    :raises RuntimeError: If any row would be refused, naming each check and how many rows it refuses.
    """
    connection = op.get_bind()
    refused: dict[str, int] = {}
    for check in checks:
        outside = text(
            f'SELECT count(*) FROM "{check.table}" WHERE "{check.column}" IS NOT NULL AND NOT ({check.condition})'
        )
        count = int(connection.execute(outside).scalar_one())
        if count:
            refused[check.name] = count
    if refused:
        err_msg = f'Rows hold values outside their enum, by check: {refused}. Nothing was changed.'
        raise RuntimeError(err_msg)


def add_value_checks(checks: Sequence[ValueCheck]) -> None:
    """Add the checks, each table rebuilt once in batch mode, as SQLite adds a constraint only to a new table.

    :param checks: The checks to add.
    :type checks: Sequence[ValueCheck]
    """
    for table, of_table in _by_table(checks).items():
        with op.batch_alter_table(table, recreate=REBUILD) as batch_op:
            for check in of_table:
                # The name is final, so the naming convention is not applied to it again
                batch_op.create_check_constraint(op.f(check.name), check.condition)


def drop_value_checks(checks: Sequence[ValueCheck]) -> None:
    """Drop the checks, each table rebuilt once in batch mode.

    :param checks: The checks to drop.
    :type checks: Sequence[ValueCheck]
    """
    for table, of_table in _by_table(checks).items():
        with op.batch_alter_table(table, recreate=REBUILD) as batch_op:
            for check in of_table:
                batch_op.drop_constraint(op.f(check.name), type_='check')


def _by_table(checks: Sequence[ValueCheck]) -> dict[str, list[ValueCheck]]:
    """Group the checks by their table, in the order of their first check.

    :param checks: The checks.
    :type checks: Sequence[ValueCheck]
    :returns: The checks of each table.
    :rtype: dict[str, list[ValueCheck]]
    """
    grouped: defaultdict[str, list[ValueCheck]] = defaultdict(list)
    for check in checks:
        grouped[check.table].append(check)
    return dict(grouped)
