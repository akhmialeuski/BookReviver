"""Column types and referential actions shared by the tables of the catalogue and of a book."""

import enum
from typing import Final

from sqlalchemy import Enum

# Referential action that lets the database remove the child rows with their parent
CASCADE: Final = 'CASCADE'
# Referential action that keeps a row whose optional parent is removed, emptying the reference
SET_NULL: Final = 'SET NULL'
# Referential action that refuses to remove a parent while rows still refer to it
RESTRICT: Final = 'RESTRICT'


def enum_by_value[EnumT: enum.Enum](enum_type: type[EnumT]) -> Enum:
    """Return a column type storing members of ``enum_type`` by value in a plain string column.

    SQLAlchemy stores enum members by name unless told otherwise, and a native enum type exists only on some
    databases. A plain string column holding the value keeps the rows readable when a member is renamed, as long as
    its value stays.

    :param enum_type: Domain enum whose members the column stores.
    :type enum_type: type[EnumT]
    :returns: Column type that writes ``member.value`` and reads it back as the member.
    :rtype: Enum
    """
    return Enum(
        enum_type,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
