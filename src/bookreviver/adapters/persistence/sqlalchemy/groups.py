"""The groups of the pages of a book, which the rows of the book name by an integer key and the domain by label.

A group is a row of ``page_groups``, so a label is stored once and every page, value and change of the group names
the row. The domain knows a group by its label alone, so the mappers translate between the two through
:class:`PageGroups`, which reads the groups of the book once per session, as the unique object recipe of SQLAlchemy
looks a row up by its natural key before making it. A group has no operation of its own: writing a label makes its
row, and :meth:`PageGroups.prune` removes a row no other row names.
"""

from typing import TYPE_CHECKING

from sqlalchemy import delete, exists, insert, or_, select

from bookreviver.adapters.persistence.sqlalchemy.tables import GroupValuesRow, PageGroupRow, PageRow, StepChangeRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PageGroups:
    """The groups of one book on the session of a transaction, translated between their labels and their keys.

    :ivar _session: Session of the transaction.
    :ivar _keys: Key of each group by its label, read on first use.
    :ivar _labels: Label of each group by its key, read with ``_keys``.
    """

    def __init__(self, session: Session) -> None:
        """Create the groups of the book on ``session``, which are read when they are first needed.

        :param session: Session of the transaction.
        :type session: Session
        """
        self._session = session
        self._keys: dict[str, int] | None = None
        self._labels: dict[int, str] = {}

    def label_of(self, group_id: int | None) -> str:
        """Return the label of a group, which is empty for no group.

        :param group_id: Key of the group, or None.
        :type group_id: int | None
        :returns: The label.
        :rtype: str
        """
        if group_id is None:
            return ''
        self._read()
        return self._labels[group_id]

    def find(self, label: str) -> int | None:
        """Return the key of the group with this label, without making one.

        :param label: Label of the group, which is empty for no group.
        :type label: str
        :returns: The key, or None for the empty label or a label no group has.
        :rtype: int | None
        """
        return self._read().get(label) if label else None

    def key_of(self, label: str) -> int | None:
        """Return the key of the group with this label, making the group when the book has none yet.

        :param label: Label of the group, which is empty for no group.
        :type label: str
        :returns: The key, or None for the empty label.
        :rtype: int | None
        """
        if not label:
            return None
        keys = self._read()
        if label not in keys:
            made = self._session.execute(insert(PageGroupRow).values(label=label).returning(PageGroupRow.id))
            keys[label] = made.scalar_one()
            self._labels[keys[label]] = label
        return keys[label]

    def prune(self) -> None:
        """Remove every group that no page, value or change names, which leaves the groups to be read again."""
        named = or_(
            exists().where(PageRow.group_id == PageGroupRow.id),
            exists().where(GroupValuesRow.group_id == PageGroupRow.id),
            exists().where(StepChangeRow.group_id == PageGroupRow.id),
        )
        self._session.execute(delete(PageGroupRow).where(~named))
        self._keys = None
        self._labels = {}

    def _read(self) -> dict[str, int]:
        """Read the groups of the book on first use.

        :returns: Key of each group by its label.
        :rtype: dict[str, int]
        """
        if self._keys is None:
            self._labels = dict(self._session.execute(select(PageGroupRow.id, PageGroupRow.label)).all())
            self._keys = {label: key for key, label in self._labels.items()}
        return self._keys
