"""The generic mapper between one kind of domain entity and the row of its table."""

from abc import ABC, abstractmethod


class RowMapper[EntityT, RowT](ABC):
    """Translation between one kind of domain entity and the row of its table.

    The generic repository of the adapter holds a mapper of this type, so one repository class serves every pair of
    entity and row.
    """

    @abstractmethod
    def to_entity(self, row: RowT) -> EntityT:
        """Build the domain entity stored in ``row``.

        :param row: Row loaded from the database.
        :type row: RowT
        :returns: Frozen entity holding every value of the row.
        :rtype: EntityT
        """

    @abstractmethod
    def to_row(self, entity: EntityT) -> RowT:
        """Build a new, unattached row holding every field of ``entity``.

        :param entity: Domain entity to store.
        :type entity: EntityT
        :returns: Transient row, ready to be added or merged into a session.
        :rtype: RowT
        """
