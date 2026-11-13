"""The steps of the recipes of a book: read from their three tables in order, and written back as the rows that changed.

A recipe is written back by comparing its steps with the places the recipe holds now. A step the recipe no longer
holds loses its place, and with it its parameters, and its row is marked removed once no recipe holds it. A step that
is new to the book gets a row, and one that comes back has the mark taken off. A step keeps the order key of its place
when it stands in the same order relative to the steps that keep theirs, which the longest run of places in rising
order gives, so moving one step rewrites the key of that step alone and a new step takes a key between its neighbours.
The switch of a step is a column of its place, and its parameters a row of its active type, each written only when it
changed. A write keeps the type of a step that has one, and refuses a recipe that names another: the type is replaced by
:meth:`RecipeSteps.replace_type` alone, which keeps the parameters of the earlier type in their rows.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, select, update

from bookreviver.adapters.persistence.sqlalchemy.mappers.recipes import RecipeStepMapper
from bookreviver.adapters.persistence.sqlalchemy.rows import RecipeStepParamsRows, RecipeStepRows, StepRows
from bookreviver.adapters.persistence.sqlalchemy.tables import RecipeRow, RecipeStepParamsRow, RecipeStepRow, StepRow
from bookreviver.domain.errors import StepTypeChangedError

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.orm import Session

    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.recipes import Recipe, Step, StepTypeChange
    from bookreviver.ports.ordering import OrderKeys


def rising_places(keys: Sequence[str | None]) -> set[int]:
    """Find the places of a longest run of keys that rise in the order the places are given, leaving out no key.

    :param keys: The order keys of the places, None for a place that has none yet.
    :type keys: Sequence[str | None]
    :returns: The indexes of the places whose keys can stay.
    :rtype: set[int]
    """
    lengths = [0] * len(keys)
    previous = [-1] * len(keys)
    for index, key in enumerate(keys):
        if key is None:
            continue
        lengths[index] = 1
        for before in range(index):
            other = keys[before]
            if other is not None and other < key and lengths[before] + 1 > lengths[index]:
                lengths[index], previous[index] = lengths[before] + 1, before
    kept: set[int] = set()
    index = max(range(len(keys)), key=lengths.__getitem__, default=-1)
    while index >= 0 and lengths[index] > 0:
        kept.add(index)
        index = previous[index]
    return kept


class RecipeSteps:
    """The steps of the recipes of a book on the session of one transaction."""

    def __init__(self, session: Session, order_keys: OrderKeys) -> None:
        """Work on the session of a transaction.

        :param session: Session of the transaction.
        :type session: Session
        :param order_keys: The order keys that place the steps of a recipe.
        :type order_keys: OrderKeys
        """
        self.session = session
        self.order_keys = order_keys
        self.steps = StepRows(session=session)
        self.places = RecipeStepRows(session=session)
        self.params = RecipeStepParamsRows(session=session)
        self.mapper = RecipeStepMapper()

    def read(self, recipe_ids: Collection[UUID]) -> dict[UUID, tuple[Step, ...]]:
        """Read the steps of some recipes, each recipe's in its order.

        :param recipe_ids: The recipes.
        :type recipe_ids: Collection[UUID]
        :returns: The steps of each recipe that has any.
        :rtype: dict[UUID, tuple[Step, ...]]
        """
        if not recipe_ids:
            return {}
        params = and_(
            RecipeStepParamsRow.recipe_id == RecipeStepRow.recipe_id,
            RecipeStepParamsRow.step_id == RecipeStepRow.step_id,
            RecipeStepParamsRow.type_key == StepRow.type_key,
        )
        statement = (
            select(RecipeStepRow, StepRow, RecipeStepParamsRow.params)
            .join(StepRow, StepRow.id == RecipeStepRow.step_id)
            .outerjoin(RecipeStepParamsRow, params)
            .where(RecipeStepRow.recipe_id.in_(recipe_ids))
            .order_by(RecipeStepRow.recipe_id, RecipeStepRow.order_key, RecipeStepRow.step_id)
        )
        found: defaultdict[UUID, list[Step]] = defaultdict(list)
        for place, step, stored in self.session.execute(statement):
            found[place.recipe_id].append(self.mapper.to_step(place, step, stored))
        return {recipe_id: tuple(steps) for recipe_id, steps in found.items()}

    def write(self, recipe: Recipe) -> None:
        """Write the steps of a recipe back, as the rows that changed.

        :param recipe: The recipe with its steps in order, whose row is stored.
        :type recipe: Recipe
        :raises ConflictError: If the recipe holds a step twice.
        :raises NotFoundError: If a step of the recipe is a step of another stage.
        """
        StepsWrite(self, recipe).apply()

    def kept_params(self, step_id: UUID, type_key: str) -> dict[UUID, MetadataMap]:
        """Read the parameters a step has under a type in the recipes that hold it.

        :param step_id: The step.
        :type step_id: UUID
        :param type_key: Key of the processor of the type.
        :type type_key: str
        :returns: The parameters of each recipe that kept parameters of the type, by recipe.
        :rtype: dict[UUID, MetadataMap]
        """
        rows = self.params.get_many(RecipeStepParamsRow.step_id == step_id, RecipeStepParamsRow.type_key == type_key)
        return {row.recipe_id: row.params for row in rows}

    def replace_type(self, change: StepTypeChange) -> None:
        """Give a step another type, store its parameters of that type in each recipe and mark those recipes changed.

        :param change: The step, its new type and the parameters of each recipe that holds it.
        :type change: StepTypeChange
        :raises NotFoundError: If the book has no such step, or a recipe of the parameters does not hold it.
        """
        self.steps.get(change.step_id).type_key = change.processor_key
        self.params.replace_many(
            [
                RecipeStepParamsRow(
                    recipe_id=recipe_id, step_id=change.step_id, type_key=change.processor_key, params=dict(params)
                )
                for recipe_id, params in change.params.items()
            ]
        )
        self.session.execute(
            update(RecipeRow)
            .where(RecipeRow.id.in_(list(change.params)))
            .values(updated_at=change.moment)
            .execution_options(synchronize_session='fetch')
        )

    def held_by(self, recipe_id: UUID) -> list[UUID]:
        """List the steps a recipe holds, in no order.

        :param recipe_id: The recipe.
        :type recipe_id: UUID
        :returns: The identifiers of its steps.
        :rtype: list[UUID]
        """
        return list(self.session.scalars(select(RecipeStepRow.step_id).where(RecipeStepRow.recipe_id == recipe_id)))

    def release(self, step_ids: Collection[UUID], moment: datetime) -> None:
        """Mark removed the steps of a list that no recipe holds any longer, which keep their rows.

        :param step_ids: The steps that left a recipe.
        :type step_ids: Collection[UUID]
        :param moment: Time they left it.
        :type moment: datetime
        """
        if not step_ids:
            return
        held = select(RecipeStepRow.step_id).where(RecipeStepRow.step_id.in_(step_ids))
        self.session.execute(
            update(StepRow)
            .where(StepRow.id.in_(step_ids), StepRow.id.not_in(held), StepRow.removed_at.is_(None))
            .values(removed_at=moment)
            .execution_options(synchronize_session='fetch')
        )


class StepsWrite:
    """The write of the steps of one recipe, against the places the recipe holds now."""

    def __init__(self, steps: RecipeSteps, recipe: Recipe) -> None:
        """Read the places the recipe holds now, in their order.

        :param steps: The steps of the recipes on the session of the transaction.
        :type steps: RecipeSteps
        :param recipe: The recipe with its new steps.
        :type recipe: Recipe
        """
        self._steps = steps
        self._recipe = recipe
        current = steps.places.list(RecipeStepRow.recipe_id == recipe.id)
        self._current = {row.step_id: row for row in sorted(current, key=lambda row: row.order_key)}

    def apply(self) -> None:
        """Take out the steps that left, store the steps and their places and parameters, and release what left.

        :raises ConflictError: If the recipe holds a step twice.
        :raises NotFoundError: If a step of the recipe is a step of another stage.
        """
        wanted = {step.step_id for step in self._recipe.steps}
        gone = [step_id for step_id in self._current if step_id not in wanted]
        if gone:
            self._steps.session.execute(
                delete(RecipeStepRow)
                .where(RecipeStepRow.recipe_id == self._recipe.id, RecipeStepRow.step_id.in_(gone))
                .execution_options(synchronize_session='fetch')
            )
        self._hold_steps()
        self._place_steps()
        self._set_params()
        self._steps.release(gone, self._recipe.updated_at)

    def _hold_steps(self) -> None:
        """Store a row for each step that has none, and give one that has a row back its place.

        A step that rows name and no recipe held was kept with no type, which the recipe gives it.

        :raises StepTypeChangedError: If the recipe names a step under another type than the one it has.
        """
        rows = self._steps.steps.get_many_in(StepRow.id, [step.step_id for step in self._recipe.steps])
        stored = {row.id: row for row in rows}
        new: list[StepRow] = []
        for step in self._recipe.steps:
            row = stored.get(step.step_id)
            if row is None:
                new.append(self._steps.mapper.step_row(self._recipe, step, self._recipe.updated_at))
                continue
            if row.type_key and row.type_key != step.processor_key:
                raise StepTypeChangedError
            row.removed_at = None
            row.type_key = step.processor_key
        if new:
            self._steps.steps.add_many(new)

    def _place_steps(self) -> None:
        """Store the place of each step, a new one with a key between its neighbours, a moved one with a new key."""
        new: list[RecipeStepRow] = []
        for step, key in zip(self._recipe.steps, self._keys(), strict=True):
            row = self._current.get(step.step_id)
            if row is None:
                new.append(self._steps.mapper.place_row(self._recipe, step, key))
                continue
            row.order_key = key
            row.enabled = step.enabled
        if new:
            self._steps.places.add_many(new)

    def _set_params(self) -> None:
        """Store the parameters of the active type of each step, the row of a type only when they changed."""
        rows = self._steps.params.list(RecipeStepParamsRow.recipe_id == self._recipe.id)
        stored = {(row.step_id, row.type_key): row for row in rows}
        new: list[RecipeStepParamsRow] = []
        for step in self._recipe.steps:
            row = stored.get((step.step_id, step.processor_key))
            if row is None:
                new.append(self._steps.mapper.params_row(self._recipe, step))
            elif row.params != dict(step.params):
                row.params = dict(step.params)
        if new:
            self._steps.params.add_many(new)

    def _keys(self) -> list[str]:
        """Give the order key of each step, the key of a step whose place keeps its order kept.

        :returns: The keys in the order of the steps, rising.
        :rtype: list[str]
        """
        places = [self._current.get(step.step_id) for step in self._recipe.steps]
        old = [None if place is None else place.order_key for place in places]
        kept = rising_places(old)
        keys = [key if index in kept else None for index, key in enumerate(old)]
        start = 0
        while start < len(keys):
            if keys[start] is not None:
                start += 1
                continue
            end = start
            while end < len(keys) and keys[end] is None:
                end += 1
            lower = keys[start - 1] if start else None
            upper = keys[end] if end < len(keys) else None
            keys[start:end] = self._steps.order_keys.spread(lower=lower, upper=upper, count=end - start)
            start = end
        return [key for key in keys if key is not None]
