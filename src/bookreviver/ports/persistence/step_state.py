"""The repository ports of what the pages keep for the steps: their settings, manual edits, values and history."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.page_steps import PageStepKey, PageStepState
from bookreviver.domain.step_values import StepValues, StepValuesKey
from bookreviver.ports.persistence.base import KeyedRepository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.history import PageHistoryEntry
    from bookreviver.domain.ids import (
        ChangeBatchId,
        PageHistoryEntryId,
        PageId,
        StepId,
    )
    from bookreviver.domain.stages import Stage


class PageStepStateRepository(KeyedRepository[PageStepState, PageStepKey]):
    """The settings and the manual edit of each step on each page, addressed by the page, the stage and the step.

    Deleting a page removes its states. Storing a state raises ``NotFoundError`` when its page is not stored, and
    ``find`` returns None when the page has neither a setting nor an edit for the step.
    """

    @abstractmethod
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepState]:
        """Return the states of one page, those of one stage or of all, by stage and step.

        :param page_id: Page owning the states.
        :type page_id: PageId
        :param stage: Stage whose states are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The states of the page.
        :rtype: Sequence[PageStepState]
        """

    @abstractmethod
    def list_for_step(self, page_ids: Collection[PageId], stage: Stage, step_id: StepId) -> Sequence[PageStepState]:
        """Return the states one step of a stage has on several pages, in one read, by page identifier.

        :param page_ids: Pages whose states are read.
        :type page_ids: Collection[PageId]
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The states of the step on those of the pages that have one.
        :rtype: Sequence[PageStepState]
        """


class StepValuesRepository(KeyedRepository[StepValues, StepValuesKey]):
    """The values of the steps for the odd pages, the even pages and the groups, addressed by book, step and part.

    Deleting a book removes them. Storing values raises ``NotFoundError`` when the book is not stored, and
    ``find`` returns None when the part of the pages changes no field of the step.
    """

    @abstractmethod
    def list_for_step(self, step_id: StepId) -> Sequence[StepValues]:
        """Return the values every part of the pages of a book has for one step, by scope and group.

        Books built from one profile share the identifiers of the steps, so the values are those of one book.

        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The values of the step, which are none when no part changes a field of it.
        :rtype: Sequence[StepValues]
        """

    @abstractmethod
    def list_all(self) -> Sequence[StepValues]:
        """Return the values of every step of a book, by step, scope and group, which is one read for all stages.

        :returns: The values of the book, each naming its stage.
        :rtype: Sequence[StepValues]
        """


class PageHistoryRepository(ABC):
    """The history of the layers of the steps of each page. A change is added once and never rewritten.

    A change of the values of the odd pages, the even pages or a group reaches every page that takes them, and is one
    entry in the history of each of those pages, the entries sharing the identifier of the change, which is stored
    once. A change is therefore addressed by its page as well as its identifier, and the history has no ``get``,
    ``update`` or ``delete`` of one change. Deleting a page removes its history, and a change that reached no other
    page with it. ``add`` and ``add_many`` number the changes of a page from one in the order they are written,
    whatever their ``sequence``, and return them as stored.
    """

    @abstractmethod
    def add(self, entity: PageHistoryEntry) -> PageHistoryEntry:
        """Store a new change of one page and return it as stored.

        :param entity: Change to store, with its identifier already assigned.
        :type entity: PageHistoryEntry
        :returns: The change as stored, with its sequence.
        :rtype: PageHistoryEntry
        :raises ConflictError: If a change with this identifier is already stored.
        :raises NotFoundError: If the page is not stored.
        """

    @abstractmethod
    def add_many(self, entities: Sequence[PageHistoryEntry]) -> Sequence[PageHistoryEntry]:
        """Store several new changes at once, all of them or, on an error, none.

        The entries that share an identifier are one change and its pages, and must agree on everything but the page.

        :param entities: Changes to store, with their identifiers already assigned.
        :type entities: Sequence[PageHistoryEntry]
        :returns: The changes as stored, with their sequences, in the given order.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If a change is stored already, or reaches one page twice.
        :raises NotFoundError: If a page is not stored.
        """

    @abstractmethod
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageHistoryEntry]:
        """Return the changes of one page, those of one stage or of all, in the order they were written (by sequence).

        :param page_id: Page the changes were made on.
        :type page_id: PageId
        :param stage: Stage whose changes are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageHistoryEntry]
        """

    @abstractmethod
    def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageHistoryEntry]:
        """Return the changes of one batch, which may span several pages, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageHistoryEntry]
        """

    @abstractmethod
    def list_undoing(self, change_ids: Collection[PageHistoryEntryId]) -> Sequence[PageHistoryEntry]:
        """Return the changes that take back any of the given changes.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageHistoryEntryId]
        :returns: The undos, by page and then by sequence; none for changes that stand.
        :rtype: Sequence[PageHistoryEntry]
        """

    @abstractmethod
    def delete_for_step(self, key: PageStepKey) -> int:
        """Delete every change of one step on one page, whatever its layer, source or batch.

        Other steps and other pages keep their changes, and the next change added to the page is numbered after the
        highest sequence the page still holds.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: How many changes were deleted.
        :rtype: int
        """
