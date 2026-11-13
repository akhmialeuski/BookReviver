"""Mappers of what the pages keep for the steps: their states, their values and their history.

The values of a step are rows of two tables, one for the odd and the even pages and one for the pages of a group, and
the history of a page is a row that names a change of a layer of a step.
"""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    GroupValuesRow,
    PageHistoryRow,
    PageStepStateRow,
    StepChangeRow,
    StepValuesRow,
)
from bookreviver.domain.geometry import geometry_from_data
from bookreviver.domain.history import PageHistoryEntry
from bookreviver.domain.ids import (
    ChangeBatchId,
    PageHistoryEntryId,
    PageId,
    StepId,
    StorageKey,
)
from bookreviver.domain.page_steps import PageEdit, PageStepState
from bookreviver.domain.step_values import StepValues, ValueScope

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.groups import PageGroups


class PageStepStateMapper(RowMapper[PageStepState, PageStepStateRow]):
    """Translation of the state of a step on a page, whose manual edit is spread over columns, its shape as JSON."""

    @override
    def to_entity(self, row: PageStepStateRow) -> PageStepState:
        """Build the state stored in ``row``.

        :param row: Page step state row loaded from the database.
        :type row: PageStepStateRow
        :returns: The state with the manual edit rebuilt from its columns, if the row has one.
        :rtype: PageStepState
        """
        edit = None
        if row.kind is not None and row.edit_hash is not None and row.edit_saved_at is not None:
            edit = PageEdit(
                page_id=PageId(row.page_id),
                stage=row.stage,
                step_id=StepId(row.step_id),
                kind=row.kind,
                geometry=None if row.geometry is None else geometry_from_data(row.kind, row.geometry),
                mask_key=None if row.mask_key is None else StorageKey(row.mask_key),
                edit_hash=row.edit_hash,
                updated_at=row.edit_saved_at,
            )
        return PageStepState(
            page_id=PageId(row.page_id),
            stage=row.stage,
            step_id=StepId(row.step_id),
            params=row.params,
            edit=edit,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: PageStepState) -> PageStepStateRow:
        """Build the row of ``entity``.

        :param entity: State to store.
        :type entity: PageStepState
        :returns: Transient page step state row.
        :rtype: PageStepStateRow
        """
        edit = entity.edit
        return PageStepStateRow(
            page_id=entity.page_id,
            stage=entity.stage,
            step_id=entity.step_id,
            params=dict(entity.params),
            kind=None if edit is None else edit.kind,
            geometry=None if edit is None or edit.geometry is None else edit.geometry.to_data(),
            mask_key=None if edit is None else edit.mask_key,
            edit_hash=None if edit is None else edit.edit_hash,
            edit_saved_at=None if edit is None else edit.updated_at,
            updated_at=entity.updated_at,
        )


class PageHistoryMapper(RowMapper[PageHistoryEntry, PageHistoryRow]):
    """Translation of a change as the history of one page names it: the row of the history and the change it links.

    A change that reaches several pages is one row of ``step_changes`` and a row of ``page_history`` for each page, and
    one entry of the domain for each page, which share the identifier of the change.

    :ivar _groups: The groups of the book.
    """

    def __init__(self, groups: PageGroups) -> None:
        """Create the mapper over the groups of the book of the session.

        :param groups: The groups of the book, which translate a label into the key of its row and back.
        :type groups: PageGroups
        """
        self._groups = groups

    @override
    def to_entity(self, row: PageHistoryRow) -> PageHistoryEntry:
        """Build the change stored in ``row`` and the change it links, which is loaded with it.

        :param row: Row of the history loaded with its change.
        :type row: PageHistoryRow
        :returns: The change as the history of the page keeps it.
        :rtype: PageHistoryEntry
        """
        change = row.change
        return PageHistoryEntry(
            id=PageHistoryEntryId(change.id),
            page_id=PageId(row.page_id),
            stage=change.stage,
            step_id=StepId(change.step_id),
            layer=change.layer,
            scope=change.scope,
            group_label=self._groups.label_of(change.group_id),
            before=change.before,
            after=change.after,
            source=change.source,
            batch_id=None if change.batch_id is None else ChangeBatchId(change.batch_id),
            undoes=None if change.undoes_id is None else PageHistoryEntryId(change.undoes_id),
            created_at=change.created_at,
            sequence=row.sequence,
        )

    @override
    def to_row(self, entity: PageHistoryEntry) -> PageHistoryRow:
        """Build the row that places the change in the history of its page.

        :param entity: Change to store, numbered in the history of its page.
        :type entity: PageHistoryEntry
        :returns: Transient row of the history.
        :rtype: PageHistoryRow
        """
        return PageHistoryRow(page_id=entity.page_id, sequence=entity.sequence, change_id=entity.id)

    def change_row(self, entity: PageHistoryEntry) -> StepChangeRow:
        """Build the row of the change itself, which every page the change reached shares.

        :param entity: Change to store.
        :type entity: PageHistoryEntry
        :returns: Transient row of the change.
        :rtype: StepChangeRow
        """
        return StepChangeRow(
            id=entity.id,
            stage=entity.stage,
            step_id=entity.step_id,
            layer=entity.layer,
            scope=entity.scope,
            group_id=self._groups.key_of(entity.group_label),
            before=None if entity.before is None else dict(entity.before),
            after=None if entity.after is None else dict(entity.after),
            source=entity.source,
            batch_id=entity.batch_id,
            undoes_id=entity.undoes,
            created_at=entity.created_at,
        )


class StepValuesMapper(RowMapper[StepValues, StepValuesRow]):
    """Translation of the values of a step for the odd or the even pages, whose fields are stored as a JSON object."""

    @override
    def to_entity(self, row: StepValuesRow) -> StepValues:
        """Build the values stored in ``row``.

        :param row: Row of the values loaded from the database.
        :type row: StepValuesRow
        :returns: The values of the step for the odd or the even pages.
        :rtype: StepValues
        """
        return StepValues(
            stage=row.stage, step_id=StepId(row.step_id), scope=row.scope, params=row.params, updated_at=row.updated_at
        )

    @override
    def to_row(self, entity: StepValues) -> StepValuesRow:
        """Build the row of ``entity``.

        :param entity: Values of the odd or the even pages to store.
        :type entity: StepValues
        :returns: Transient row of the values.
        :rtype: StepValuesRow
        """
        return StepValuesRow(
            step_id=entity.step_id,
            scope=entity.scope,
            stage=entity.stage,
            params=dict(entity.params),
            updated_at=entity.updated_at,
        )


class GroupValuesMapper(RowMapper[StepValues, GroupValuesRow]):
    """Translation of the values of a step for a group, whose group is named by its key.

    :ivar _groups: The groups of the book.
    """

    def __init__(self, groups: PageGroups) -> None:
        """Create the mapper over the groups of the book of the session.

        :param groups: The groups of the book, which translate a label into the key of its row and back.
        :type groups: PageGroups
        """
        self._groups = groups

    @override
    def to_entity(self, row: GroupValuesRow) -> StepValues:
        """Build the values stored in ``row``.

        :param row: Row of the values loaded from the database.
        :type row: GroupValuesRow
        :returns: The values of the step for the group.
        :rtype: StepValues
        """
        return StepValues(
            stage=row.stage,
            step_id=StepId(row.step_id),
            scope=ValueScope.GROUP,
            group_label=self._groups.label_of(row.group_id),
            params=row.params,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: StepValues) -> GroupValuesRow:
        """Build the row of ``entity``, making its group when the book has none of this label yet.

        :param entity: Values of a group to store.
        :type entity: StepValues
        :returns: Transient row of the values.
        :rtype: GroupValuesRow
        """
        return GroupValuesRow(
            step_id=entity.step_id,
            group_id=self._groups.key_of(entity.group_label),
            stage=entity.stage,
            params=dict(entity.params),
            updated_at=entity.updated_at,
        )
