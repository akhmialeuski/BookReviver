"""Repositories of what the pages keep for the steps: their states, their values and the history of their changes.

The values of the odd and the even pages and the values of a group are rows of two tables that one repository serves,
and the history of a page names the changes that reached it, each stored once.
"""

from itertools import batched
from typing import TYPE_CHECKING, Any, cast, override

from attrs import evolve
from sqlalchemy import delete, func, select
from sqlalchemy.orm import contains_eager

from bookreviver.adapters.persistence.sqlalchemy.mappers import (
    GroupValuesMapper,
    PageHistoryMapper,
    PageStepStateMapper,
    StepValuesMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyKeyedRepository
from bookreviver.adapters.persistence.sqlalchemy.rows import (
    GroupValuesRows,
    PageHistoryRows,
    PageStepStateRows,
    StepChangeRows,
    StepValuesRows,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    GroupValuesRow,
    PageHistoryRow,
    PageStepStateRow,
    StepChangeRow,
    StepValuesRow,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import StepId
from bookreviver.domain.page_steps import PageStepKey, PageStepState
from bookreviver.domain.stages import Stage
from bookreviver.domain.step_values import StepValues, ValueScope
from bookreviver.ports.persistence import (
    PageHistoryRepository,
    PageStepStateRepository,
    StepValuesRepository,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from sqlalchemy import ColumnElement, CursorResult
    from sqlalchemy.orm import InstrumentedAttribute, Session

    from bookreviver.adapters.persistence.sqlalchemy.groups import PageGroups
    from bookreviver.domain.history import PageHistoryEntry
    from bookreviver.domain.ids import (
        ChangeBatchId,
        PageHistoryEntryId,
        PageId,
    )
    from bookreviver.domain.step_values import StepValuesKey
    from bookreviver.ports.persistence import KeyedRepository


class SqlAlchemyPageStepStateRepository(
    SqlAlchemyKeyedRepository[PageStepState, PageStepKey, PageStepStateRow], PageStepStateRepository
):
    """Settings and manual edits, addressed by the page, the stage and the step."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_step_states`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=PageStepStateRows(session=session), mapper=PageStepStateMapper())

    @override
    def get(self, entity_id: PageStepKey) -> PageStepState:
        """Return the state of one step on one page.

        :param entity_id: Page, stage and step.
        :type entity_id: PageStepKey
        :returns: The stored state.
        :rtype: PageStepState
        :raises NotFoundError: If the page has no state for the step.
        """
        row = self._rows.get((entity_id.page_id, entity_id.stage, entity_id.step_id))
        return self._mapper.to_entity(row)

    @override
    def delete(self, entity_id: PageStepKey) -> None:
        """Remove the state of one step on one page.

        :param entity_id: Page, stage and step.
        :type entity_id: PageStepKey
        :raises NotFoundError: If the page has no state for the step.
        """
        self._rows.delete((entity_id.page_id, entity_id.stage, entity_id.step_id))

    @override
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepState]:
        """Return the states of one page, by stage and step.

        :param page_id: Page owning the states.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :returns: The states of the page.
        :rtype: Sequence[PageStepState]
        """
        filters: list[ColumnElement[bool]] = [PageStepStateRow.page_id == page_id]
        if stage is not None:
            filters.append(PageStepStateRow.stage == stage)
        order = list(Stage)
        states = [self._mapper.to_entity(row) for row in self._rows.get_many(*filters)]
        return sorted(states, key=lambda state: (order.index(state.stage), str(state.step_id)))

    @override
    def list_for_step(self, page_ids: Collection[PageId], stage: Stage, step_id: StepId) -> Sequence[PageStepState]:
        """Return the states one step of a stage has on several pages in one ``IN`` query, by page identifier.

        :param page_ids: Pages whose states are read.
        :type page_ids: Collection[PageId]
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The states of the step on those of the pages that have one.
        :rtype: Sequence[PageStepState]
        """
        rows = self._rows.get_many_in(
            PageStepStateRow.page_id, page_ids, order=[PageStepStateRow.page_id], stage=stage, step_id=step_id
        )
        return [self._mapper.to_entity(row) for row in rows]


class SqlAlchemyStepValuesRepository(StepValuesRepository):
    """The values of the steps for the odd pages, the even pages and the groups, addressed by step and part.

    The values of the odd and the even pages are rows of ``step_values`` and those of a group rows of
    ``group_values``, so every key of a row is a primary key with no null in it. Each table has a keyed repository of
    its own, and the key of the values says which one holds them.

    :ivar _groups: The groups of the book on the session.
    :ivar _parity_rows: The rows of the values of the odd and the even pages.
    :ivar _group_rows: The rows of the values of the groups.
    :ivar _parity_mapper: Translation of the values of the odd and the even pages.
    :ivar _group_mapper: Translation of the values of a group.
    :ivar _parity: The values of the odd and the even pages, under the primary key of their table.
    :ivar _grouped: The values of the groups, under the primary key of their table.
    """

    def __init__(self, session: Session, groups: PageGroups) -> None:
        """Create the repository over the ``step_values`` and the ``group_values`` tables.

        :param session: Session of the transaction.
        :type session: Session
        :param groups: The groups of the book on the session, which name the group of the values of a group.
        :type groups: PageGroups
        """
        self._groups = groups
        self._parity_rows = StepValuesRows(session=session)
        self._group_rows = GroupValuesRows(session=session)
        self._parity_mapper = StepValuesMapper()
        self._group_mapper = GroupValuesMapper(groups)
        self._parity = SqlAlchemyKeyedRepository[StepValues, tuple[StepId, ValueScope], StepValuesRow](
            rows=self._parity_rows, mapper=self._parity_mapper
        )
        self._grouped = SqlAlchemyKeyedRepository[StepValues, tuple[StepId, int], GroupValuesRow](
            rows=self._group_rows, mapper=self._group_mapper
        )

    def _group_key(self, key: StepValuesKey) -> tuple[StepId, int]:
        """Order the key of the values of a group as the primary key of ``group_values``.

        :param key: Key of the values of a group.
        :type key: StepValuesKey
        :returns: The step and the key of the group.
        :rtype: tuple[StepId, int]
        :raises NotFoundError: If the book has no group of this label, so no values are stored for it.
        """
        if (group_id := self._groups.find(key.group_label)) is None:
            raise NotFoundError(key)
        return key.step_id, group_id

    def _each_table(
        self,
        entities: Sequence[StepValues],
        write: Callable[[KeyedRepository[StepValues, Any], Sequence[StepValues]], Sequence[StepValues]],
    ) -> Sequence[StepValues]:
        """Write the values of the odd and the even pages and the values of the groups each to its table.

        :param entities: The values to write.
        :type entities: Sequence[StepValues]
        :param write: The write of several values to one table.
        :type write: Callable[[KeyedRepository[StepValues, Any], Sequence[StepValues]], Sequence[StepValues]]
        :returns: The values as stored, in the given order.
        :rtype: Sequence[StepValues]
        """
        parity = [values for values in entities if values.scope is not ValueScope.GROUP]
        grouped = [values for values in entities if values.scope is ValueScope.GROUP]
        stored = {values.key: values for values in (*write(self._parity, parity), *write(self._grouped, grouped))}
        return [stored[values.key] for values in entities]

    @override
    def get(self, entity_id: StepValuesKey) -> StepValues:
        """Return the values one part of the pages has for one step.

        :param entity_id: The step and the part of the pages.
        :type entity_id: StepValuesKey
        :returns: The stored values.
        :rtype: StepValues
        :raises NotFoundError: If the part changes no field of the step.
        """
        if entity_id.scope is ValueScope.GROUP:
            return self._grouped.get(self._group_key(entity_id))
        return self._parity.get((entity_id.step_id, entity_id.scope))

    @override
    def find(self, key: StepValuesKey) -> StepValues | None:
        """Return the values one part of the pages has for one step, read as :meth:`get` reads them.

        :param key: The step and the part of the pages.
        :type key: StepValuesKey
        :returns: The values, or None.
        :rtype: StepValues | None
        """
        try:
            return self.get(key)
        except NotFoundError:
            return None

    @override
    def add(self, entity: StepValues) -> StepValues:
        """Store the values of a part of the pages that has none for the step yet.

        :param entity: The values to store.
        :type entity: StepValues
        :returns: The values as stored.
        :rtype: StepValues
        :raises ConflictError: If the part has values for the step already.
        """
        [stored] = self.add_many([entity])
        return stored

    @override
    def add_many(self, entities: Sequence[StepValues]) -> Sequence[StepValues]:
        """Store the values of several parts of the pages that have none for their steps yet.

        :param entities: The values to store.
        :type entities: Sequence[StepValues]
        :returns: The values as stored, in the given order.
        :rtype: Sequence[StepValues]
        :raises ConflictError: If a part has values for its step already.
        """
        return self._each_table(entities, lambda table, part: table.add_many(part))

    @override
    def update(self, entity: StepValues) -> StepValues:
        """Replace the values a part of the pages has for a step.

        :param entity: The values with their new fields.
        :type entity: StepValues
        :returns: The values as stored.
        :rtype: StepValues
        :raises NotFoundError: If the part has no values for the step.
        """
        if entity.scope is ValueScope.GROUP:
            self._group_key(entity.key)
            return self._grouped.update(entity)
        return self._parity.update(entity)

    @override
    def save_many(self, entities: Sequence[StepValues]) -> Sequence[StepValues]:
        """Store the values of several parts of the pages, each replacing those under its key, all of them or none.

        :param entities: The values to store.
        :type entities: Sequence[StepValues]
        :returns: The values as stored, in the given order.
        :rtype: Sequence[StepValues]
        """
        return self._each_table(entities, lambda table, part: table.save_many(part))

    @override
    def delete(self, entity_id: StepValuesKey) -> None:
        """Remove the values one part of the pages has for one step.

        :param entity_id: The step and the part of the pages.
        :type entity_id: StepValuesKey
        :raises NotFoundError: If the part changes no field of the step.
        """
        if entity_id.scope is ValueScope.GROUP:
            self._grouped.delete(self._group_key(entity_id))
        else:
            self._parity.delete((entity_id.step_id, entity_id.scope))

    def _listed(self, **filters: Any) -> list[StepValues]:
        """Read the values of both tables that match the filters, ordered by step, scope and group.

        :param filters: Column values every row read has.
        :type filters: Any
        :returns: The values.
        :rtype: list[StepValues]
        """
        order = list(ValueScope)
        found = [
            *(self._parity_mapper.to_entity(row) for row in self._parity_rows.get_many(**filters)),
            *(self._group_mapper.to_entity(row) for row in self._group_rows.get_many(**filters)),
        ]
        return sorted(found, key=lambda values: (str(values.step_id), order.index(values.scope), values.group_label))

    @override
    def list_for_step(self, step_id: StepId) -> Sequence[StepValues]:
        """Return the values every part of the pages has for one step, by scope and group.

        :param step_id: The step.
        :type step_id: StepId
        :returns: The values of the step.
        :rtype: Sequence[StepValues]
        """
        return self._listed(step_id=step_id)

    @override
    def list_all(self) -> Sequence[StepValues]:
        """Return the values of every step of the book, by step, scope and group.

        :returns: The values of the book.
        :rtype: Sequence[StepValues]
        """
        return self._listed()


class SqlAlchemyPageHistoryRepository(PageHistoryRepository):
    """The history of the layers of the steps of the pages: each change once, and its place in each page it reached.

    :ivar _links: The rows of ``page_history``, which place a change in the history of a page.
    :ivar _changes: The rows of ``step_changes``, one for each change.
    :ivar _mapper: Translation between an entry of the history of a page and its two rows.
    """

    def __init__(self, session: Session, groups: PageGroups) -> None:
        """Create the repository over the ``page_history`` and the ``step_changes`` tables.

        :param session: Session of the transaction.
        :type session: Session
        :param groups: The groups of the book on the session, which name the group of a change of a group.
        :type groups: PageGroups
        """
        self._links = PageHistoryRows(session=session)
        self._changes = StepChangeRows(session=session)
        self._mapper = PageHistoryMapper(groups)

    def _entries(
        self, *filters: ColumnElement[bool], order: Sequence[InstrumentedAttribute[Any]]
    ) -> list[PageHistoryEntry]:
        """Read the entries of the histories that match the filters, each with its change in the same statement.

        :param filters: Conditions over the rows of the history and of the changes.
        :type filters: ColumnElement[bool]
        :param order: The columns the entries are ordered by, ascending.
        :type order: Sequence[InstrumentedAttribute[Any]]
        :returns: The entries.
        :rtype: list[PageHistoryEntry]
        """
        statement = (
            select(PageHistoryRow)
            .join(PageHistoryRow.change)
            .options(contains_eager(PageHistoryRow.change))
            .where(*filters)
            .order_by(*(column.asc() for column in order))
        )
        return [self._mapper.to_entity(row) for row in self._links.session.scalars(statement)]

    @override
    def add(self, entity: PageHistoryEntry) -> PageHistoryEntry:
        """Store a change, numbering it after the last change of its page.

        :param entity: Change to store.
        :type entity: PageHistoryEntry
        :returns: The change as stored, with its sequence.
        :rtype: PageHistoryEntry
        :raises ConflictError: If a change with this identifier is stored already.
        :raises NotFoundError: If the page is not stored.
        """
        [stored] = self.add_many([entity])
        return stored

    @override
    def add_many(self, entities: Sequence[PageHistoryEntry]) -> Sequence[PageHistoryEntry]:
        """Store several changes, numbering each after the last change of its page in the order given.

        The entries that share an identifier are one change reaching several pages, which is stored once.

        :param entities: Changes to store.
        :type entities: Sequence[PageHistoryEntry]
        :returns: The changes as stored, with their sequences.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If a change is stored already, or reaches a page twice.
        :raises NotFoundError: If a page is not stored.
        """
        if not entities:
            return entities
        next_of: dict[PageId, int] = {}
        numbered: list[PageHistoryEntry] = []
        for change in entities:
            if change.page_id not in next_of:
                last = self._links.session.scalar(
                    select(func.max(PageHistoryRow.sequence)).where(PageHistoryRow.page_id == change.page_id)
                )
                next_of[change.page_id] = (last or 0) + 1
            numbered.append(evolve(change, sequence=next_of[change.page_id]))
            next_of[change.page_id] += 1
        changes = {change.id: change for change in reversed(numbered)}
        self._changes.add_many([self._mapper.change_row(change) for change in changes.values()])
        self._links.add_many([self._mapper.to_row(change) for change in numbered])
        return numbered

    @override
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageHistoryEntry]:
        """Return the changes of one page, by their sequence.

        :param page_id: Page the changes reached.
        :type page_id: PageId
        :param stage: Stage whose changes are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageHistoryEntry]
        """
        filters: list[ColumnElement[bool]] = [PageHistoryRow.page_id == page_id]
        if stage is not None:
            filters.append(StepChangeRow.stage == stage)
        return self._entries(*filters, order=[PageHistoryRow.sequence])

    @override
    def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageHistoryEntry]:
        """Return the changes of one batch, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageHistoryEntry]
        """
        by_page = [PageHistoryRow.page_id, PageHistoryRow.sequence]
        return self._entries(StepChangeRow.batch_id == batch_id, order=by_page)

    @override
    def list_undoing(self, change_ids: Collection[PageHistoryEntryId]) -> Sequence[PageHistoryEntry]:
        """Return the changes that take back any of the given changes, by page and then by sequence.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageHistoryEntryId]
        :returns: The undos.
        :rtype: Sequence[PageHistoryEntry]
        """
        found = [
            entry
            for chunk in batched(set(change_ids), self._links.IN_CHUNK_SIZE, strict=False)
            for entry in self._entries(StepChangeRow.undoes_id.in_(chunk), order=[])
        ]
        return sorted(found, key=lambda entry: (entry.page_id, entry.sequence))

    @override
    def delete_for_step(self, key: PageStepKey) -> int:
        """Delete every change of one step on one page, and the changes that reached no other page.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: How many changes the history of the page lost.
        :rtype: int
        """
        session = self._links.session
        of_step = select(StepChangeRow.id).where(StepChangeRow.stage == key.stage, StepChangeRow.step_id == key.step_id)
        reached = session.scalars(
            select(PageHistoryRow.change_id).where(
                PageHistoryRow.page_id == key.page_id, PageHistoryRow.change_id.in_(of_step)
            )
        ).all()
        statement = delete(PageHistoryRow).where(
            PageHistoryRow.page_id == key.page_id, PageHistoryRow.change_id.in_(of_step)
        )
        # An ORM delete of a mapped class returns the cursor result, whose row count the database reports
        removed = cast('CursorResult[Any]', session.execute(statement)).rowcount
        self._changes.delete_unlinked(reached)
        return removed
