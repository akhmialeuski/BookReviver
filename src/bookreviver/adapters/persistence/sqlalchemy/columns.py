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


def enum_by_value[EnumT: enum.Enum](enum_type: type[EnumT], *, name: str | None = None) -> Enum:
    """Return a column type storing members of ``enum_type`` by value in a plain string column the database checks.

    SQLAlchemy stores enum members by name unless told otherwise, and a native enum type exists only on some
    databases. A plain string column holding the value keeps the rows readable when a member is renamed, as long as
    its value stays. The column carries a ``CHECK`` of its values, so a row written past the ORM, by plain SQL or a
    migration, cannot hold a value the enum lacks. The naming convention names the check after the type, so a table
    with two columns of one enum gives each its own ``name``.

    :param enum_type: Domain enum whose members the column stores.
    :type enum_type: type[EnumT]
    :param name: Name of the check, or None for the name of the enum in lower case, as SQLAlchemy gives it.
    :type name: str | None
    :returns: Column type that writes ``member.value`` and reads it back as the member.
    :rtype: Enum
    """
    # SQLAlchemy names the type after the enum only when no name is passed at all, even None
    names = {} if name is None else {'name': name}
    return Enum(
        enum_type,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
        **names,
    )
