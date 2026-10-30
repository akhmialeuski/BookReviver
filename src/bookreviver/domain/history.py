"""The history of one step on one page, read as a stack of changes that can be taken back.

The history only grows. A change is taken back by a later change whose source is an undo and which names it, so the
changes that still stand are those no undo names, and the undos themselves are never taken back. What can be undone is
the newest change that stands, then the one before it, which is how a Ctrl+Z works, and a change of a batch is taken
back together with the rest of its batch. A value set for several pages, a value set for the odd pages, the even
pages or a group, which every page they reach keeps a change of, and a carry-over of a shape to other pages are such
batches.

The one exception to the growth is an explicit clear of one step on one page, which deletes that history, takes the
settings and the edit of the step away from the page, and deletes the results of the step on it with the results that
read them. The changes of a batch on other pages stay, and an undo of the batch takes back only the changes it still
finds.
"""

from typing import TYPE_CHECKING, Self
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import PageHistoryEntryId
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.page_steps import PageStepKey, StepLayer
from bookreviver.domain.step_values import ValueScope

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.ids import ChangeBatchId, PageId, StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageStepState
    from bookreviver.domain.stages import Stage
    from bookreviver.domain.step_values import StepValues
    from bookreviver.domain.versions import PageVersion


class ChangeSource(LabeledStrEnum):
    """What made a change of a layer of a step on a page."""

    USER = 'user', 'The user'
    RUN = 'run', 'A run'
    CARRY_OVER = 'carry-over', 'A carry-over from another page'
    UNDO = 'undo', 'An undo of an earlier change'


@frozen(kw_only=True)
class PageHistoryEntry:
    """One change of a layer of a step on a page, which the history of the page keeps and never rewrites.

    The values are the whole content of the layer before and after the change, so undoing a change writes ``before``
    back without knowing how the layer is built. The layer of the settings holds the fields the page changes, by name.

    :ivar id: Identifier of the change.
    :ivar page_id: Page the change was made on.
    :ivar stage: Stage of the step.
    :ivar step_id: The step whose layer changed.
    :ivar layer: The layer that changed.
    :ivar scope: Whose settings the layer holds: those of the page itself, or those of the odd pages, the even pages or
                 a group the page takes values from, which the history of each page they change keeps a change of.
    :ivar group_label: Label of the group for the scope of a group, and empty for the others.
    :ivar before: Content of the layer before the change, or None when the layer was empty.
    :ivar after: Content of the layer after the change, or None when the change emptied it.
    :ivar source: What made the change.
    :ivar batch_id: Identifier shared by the changes of one batch, which are undone together, or None.
    :ivar undoes: The change this one takes back, for a change whose source is an undo, or None. The history is never
                  rewritten, so a change is undone when another change names it here.
    :ivar created_at: When the change was made.
    :ivar sequence: Place of the change in the history of its page, from one, which the repository gives it when it is
                    added, so changes made at the same instant keep the order they were written in.
    """

    id: PageHistoryEntryId
    page_id: PageId
    stage: Stage
    step_id: StepId
    layer: StepLayer
    scope: ValueScope = ValueScope.PAGES
    group_label: str = ''
    before: MetadataMap | None = None
    after: MetadataMap | None = None
    source: ChangeSource
    batch_id: ChangeBatchId | None = None
    undoes: PageHistoryEntryId | None = None
    created_at: datetime
    sequence: int = 0

    @property
    def key(self) -> PageStepKey:
        """The key of the state of the step on the page that the change was made on."""
        return PageStepKey(self.page_id, self.stage, self.step_id)

    @classmethod
    def between(cls, before: PageStepState, after: PageStepState, layer: StepLayer, source: ChangeSource) -> Self:
        """Describe what the change of one layer from a state to another did.

        :param before: The state of the step on the page before the change, which is empty for a page with none.
        :type before: PageStepState
        :param after: The state after the change, which carries the time of the change.
        :type after: PageStepState
        :param layer: The layer that changed.
        :type layer: StepLayer
        :param source: What made the change.
        :type source: ChangeSource
        :returns: The change, with a new identifier, no batch, nothing undone and no sequence yet.
        :rtype: Self
        """
        return cls(
            id=PageHistoryEntryId(uuid4()),
            page_id=after.page_id,
            stage=after.stage,
            step_id=after.step_id,
            layer=layer,
            before=before.layer(layer),
            after=after.layer(layer),
            source=source,
            created_at=after.updated_at,
        )

    def taken_back(self, moment: datetime, batch_id: ChangeBatchId | None) -> Self:
        """Describe the undo of this change: the same layer changed from what it left to what it replaced.

        :param moment: When the change is taken back.
        :type moment: datetime
        :param batch_id: The batch the undos of one action share, or None for an undo of one change.
        :type batch_id: ChangeBatchId | None
        :returns: The undo, with a new identifier, naming this change and with no sequence yet.
        :rtype: Self
        """
        return evolve(
            self,
            id=PageHistoryEntryId(uuid4()),
            before=self.after,
            after=self.before,
            source=ChangeSource.UNDO,
            batch_id=batch_id,
            undoes=self.id,
            created_at=moment,
            sequence=0,
        )

    @classmethod
    def of_values(
        cls, pages: Sequence[PageId], before: StepValues, after: StepValues, source: ChangeSource
    ) -> list[Self]:
        """Describe a change of the values of a part of the pages, once for each page whose history keeps it.

        The entries are one change, so they share its identifier and differ only in the page.

        :param pages: The pages whose histories keep the change, which are those that take the values.
        :type pages: Sequence[PageId]
        :param before: The values of the part before the change, which are empty for a part that had none.
        :type before: StepValues
        :param after: The values after the change, which carry the time of the change.
        :type after: StepValues
        :param source: What made the change.
        :type source: ChangeSource
        :returns: The change for each page, with one new identifier, no batch, nothing undone and no sequence yet.
        :rtype: list[Self]
        """
        change_id = PageHistoryEntryId(uuid4())
        return [
            cls(
                id=change_id,
                page_id=page_id,
                stage=after.stage,
                step_id=after.step_id,
                layer=StepLayer.SETTINGS,
                scope=after.scope,
                group_label=after.group_label,
                before=dict(before.params) or None,
                after=dict(after.params) or None,
                source=source,
                created_at=after.updated_at,
            )
            for page_id in pages
        ]


@frozen
class StepHistory:
    """The changes of one step on one page, oldest first.

    :ivar changes: The changes in the order they were written, which is the order of their sequence.
    """

    changes: tuple[PageHistoryEntry, ...]

    @property
    def undone(self) -> frozenset[PageHistoryEntryId]:
        """The identifiers of the changes an undo took back."""
        return frozenset(change.undoes for change in self.changes if change.undoes is not None)

    @property
    def standing(self) -> tuple[PageHistoryEntry, ...]:
        """The changes that can still be undone: not an undo and not taken back, oldest first."""
        undone = self.undone
        return tuple(
            change for change in self.changes if change.source is not ChangeSource.UNDO and change.id not in undone
        )

    def back_to(self, change_id: PageHistoryEntryId) -> Sequence[PageHistoryEntry]:
        """Return the changes an undo back to one change takes back: it and every standing change after it.

        :param change_id: The oldest change to take back.
        :type change_id: PageHistoryEntryId
        :returns: The standing changes from the newest down to the one named.
        :rtype: Sequence[PageHistoryEntry]
        :raises NotFoundError: If the change is not in this history, is an undo, or was taken back already.
        """
        standing = self.standing
        for index, change in enumerate(standing):
            if change.id == change_id:
                return tuple(reversed(standing[index:]))
        raise NotFoundError(change_id)


@frozen
class CarryOver:
    """What carrying the shape a page set by hand over to other pages did, which is one batch of the history.

    :ivar batch_id: The batch the changes share, so taking back one of them takes back all of them.
    :ivar changes: The changes written, one on each page that took the shape, which are none when every page already
                   had it or was skipped.
    :ivar skipped: The pages that were left as they were because they have a shape of their own.
    """

    batch_id: ChangeBatchId
    changes: tuple[PageHistoryEntry, ...]
    skipped: tuple[PageId, ...]


@frozen
class ValueChanges:
    """What setting a value of a step for a part of the pages, or taking it back, did, as one batch of the history.

    :ivar batch_id: The batch the changes share, so taking back one of them takes back all of them.
    :ivar changes: The changes written, one on each page that the value reaches, which are none when no page changes.
    """

    batch_id: ChangeBatchId
    changes: tuple[PageHistoryEntry, ...]


@frozen
class ClearedStep:
    """What clearing a step on a page deleted.

    :ivar changes: How many changes of the history were deleted.
    :ivar versions: The versions deleted: the ones the step made on the page and every version that read one of them.
    """

    changes: int
    versions: tuple[PageVersion, ...]
