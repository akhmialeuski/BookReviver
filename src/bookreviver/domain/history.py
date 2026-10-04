"""The history of one step on one page, read as a stack of changes that can be taken back.

The history only grows. A change is taken back by a later change whose source is an undo and which names it, so the
changes that still stand are those no undo names, and the undos themselves are never taken back. What can be undone is
the newest change that stands, then the one before it, which is how a Ctrl+Z works, and a change of a batch is taken
back together with the rest of its batch.
"""

from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.domain.enums import ChangeSource
from bookreviver.domain.errors import NotFoundError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import PageStepChange
    from bookreviver.domain.ids import PageStepChangeId


@frozen
class StepHistory:
    """The changes of one step on one page, oldest first.

    :ivar changes: The changes in the order they were written, which is the order of their sequence.
    """

    changes: tuple[PageStepChange, ...]

    @property
    def undone(self) -> frozenset[PageStepChangeId]:
        """The identifiers of the changes an undo took back."""
        return frozenset(change.undoes for change in self.changes if change.undoes is not None)

    @property
    def standing(self) -> tuple[PageStepChange, ...]:
        """The changes that can still be undone: not an undo and not taken back, oldest first."""
        undone = self.undone
        return tuple(
            change for change in self.changes if change.source is not ChangeSource.UNDO and change.id not in undone
        )

    def back_to(self, change_id: PageStepChangeId) -> Sequence[PageStepChange]:
        """Return the changes an undo back to one change takes back: it and every standing change after it.

        :param change_id: The oldest change to take back.
        :type change_id: PageStepChangeId
        :returns: The standing changes from the newest down to the one named.
        :rtype: Sequence[PageStepChange]
        :raises NotFoundError: If the change is not in this history, is an undo, or was taken back already.
        """
        standing = self.standing
        for index, change in enumerate(standing):
            if change.id == change_id:
                return tuple(reversed(standing[index:]))
        raise NotFoundError(change_id)
