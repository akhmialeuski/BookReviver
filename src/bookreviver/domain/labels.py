"""The base of every closed set of values in the domain: a string enum whose members carry a label."""

import enum
from typing import Self


class LabeledStrEnum(enum.StrEnum):
    """A string enum whose members are declared as ``(value, label)`` pairs.

    :ivar label: Human-readable name of the member, shown in the interface and in error messages.
    """

    label: str

    def __new__(cls, value: str, label: str = '') -> Self:
        """Create a member whose string value is ``value`` and whose label is ``label``.

        Looking a member up by value, as in ``Stage('import')``, does not call this, so the default only keeps that
        call well-typed.

        :param value: String value of the member, stored and sent over the API.
        :type value: str
        :param label: Human-readable name of the member.
        :type label: str
        :returns: The new member.
        :rtype: Self
        """
        member = str.__new__(cls, value)
        member._value_ = value
        member.label = label
        return member
