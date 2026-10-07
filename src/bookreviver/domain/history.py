"""The history of one step on one page, read as a stack of changes that can be taken back.

The history only grows. A change is taken back by a later change whose source is an undo and which names it, so the
changes that still stand are those no undo names, and the undos themselves are never taken back. What can be undone is
the newest change that stands, then the one before it, which is how a Ctrl+Z works, and a change of a batch is taken
back together with the rest of its batch. A carry-over of a setting to other pages and a reset of steps to their
defaults are such batches.

The one exception to the growth is an explicit clear of one step on one page, which deletes that history, takes the
settings and the edit of the step away from the page, and deletes the results of the step on it with the results that
read them. The changes of a batch on other pages stay, and an undo of the batch takes back only the changes it still
finds.
"""

from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.domain.enums import ChangeSource
from bookreviver.domain.errors import NotFoundError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import PageStepChange, PageVersion
    from bookreviver.domain.ids import ChangeBatchId, PageId, PageStepChangeId


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


@frozen
class CarryOver:
    """What carrying a setting of one page over to other pages did, which is one batch of the history.

    :ivar batch_id: The batch the changes share, so taking back one of them takes back all of them.
    :ivar changes: The changes written, one on each page that took the value, which are none when every page already
                   had it or was skipped.
    :ivar skipped: The pages that were left as they were because they have a value of their own for the field.
    """

    batch_id: ChangeBatchId
    changes: tuple[PageStepChange, ...]
    skipped: tuple[PageId, ...]


@frozen
class StepReset:
    """What a reset of steps to their defaults did, which is one batch of the history.

    :ivar batch_id: The batch the changes share, so taking back one of them takes back all of them.
    :ivar changes: The changes written, one for each layer a page lost, which are none when nothing was set.
    """

    batch_id: ChangeBatchId
    changes: tuple[PageStepChange, ...]


@frozen
class ClearedStep:
    """What clearing a step on a page deleted.

    :ivar changes: How many changes of the history were deleted.
    :ivar versions: The versions deleted: the ones the step made on the page and every version that read one of them.
    """

    changes: int
    versions: tuple[PageVersion, ...]
