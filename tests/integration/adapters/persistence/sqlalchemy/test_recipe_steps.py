"""Tests for the rows a recipe writes back: a change of its steps rewrites the rows of the steps that changed alone."""

from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from sqlalchemy import select

from bookreviver.adapters.persistence.sqlalchemy.tables import RecipeStepParamsRow, RecipeStepRow, StepRow
from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.recipes import Step
from tests.harness.builders.domain import DESKEW, make_page, make_recipe, make_record
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.orm import Session

    from bookreviver.domain.ids import AccountId, BookId
    from bookreviver.domain.recipes import Recipe
    from tests.harness.adapters.database import BookDatabasesFactory, BookSessions, CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

# The parameter of the deskew step the steps of a test differ by
MAX_ANGLE: str = 'max_angle_deg'


class StepRows(NamedTuple):
    """The rows of the steps of a book, by their keys.

    :ivar steps: The type and the time of removal of each step.
    :ivar places: The order key and the switch of each step in each recipe.
    :ivar params: The parameters of each type of each step in each recipe.
    """

    steps: dict[UUID, tuple[str, datetime | None]]
    places: dict[tuple[UUID, UUID], tuple[str, bool]]
    params: dict[tuple[UUID, UUID, str], dict[str, Any]]

    @classmethod
    def read(cls, session: Session) -> StepRows:
        """Read the rows of the steps of the book of a session.

        :param session: Session of a transaction of the book.
        :type session: Session
        :returns: The rows.
        :rtype: StepRows
        """
        return cls(
            steps={row.id: (row.type_key, row.removed_at) for row in session.scalars(select(StepRow))},
            places={
                (row.recipe_id, row.step_id): (row.order_key, row.enabled)
                for row in session.scalars(select(RecipeStepRow))
            },
            params={
                (row.recipe_id, row.step_id, row.type_key): row.params
                for row in session.scalars(select(RecipeStepParamsRow))
            },
        )

    def changed(self, other: StepRows) -> tuple[set[Any], set[Any], set[Any]]:
        """Give the keys of the rows that differ from the rows of another read, of each table.

        :param other: The rows of a later read.
        :type other: StepRows
        :returns: The keys of the steps, the places and the parameters that were added, changed or deleted.
        :rtype: tuple[set[Any], set[Any], set[Any]]
        """
        return (
            _changed_keys(self.steps, other.steps),
            _changed_keys(self.places, other.places),
            _changed_keys(self.params, other.params),
        )


def _changed_keys(before: dict[Any, Any], after: dict[Any, Any]) -> set[Any]:
    """Give the keys of the rows that were added, changed or deleted between two reads of a table.

    :param before: The rows of the earlier read, by key.
    :type before: dict[Any, Any]
    :param after: The rows of the later read, by key.
    :type after: dict[Any, Any]
    :returns: The keys.
    :rtype: set[Any]
    """
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


class Book(NamedTuple):
    """A stored book, and what reads and changes it.

    :ivar book_id: The book.
    :ivar databases: Opens the databases of the books of the test.
    :ivar sessions: Sessions of the files of the books of the test.
    """

    book_id: BookId
    databases: BookDatabasesFactory
    sessions: BookSessions

    async def store(self, recipe: Recipe, *, new: bool = False) -> StepRows:
        """Store a recipe, new or changed, and read the rows of the steps after it.

        :param recipe: The recipe.
        :type recipe: Recipe
        :param new: Whether the recipe is added rather than updated.
        :type new: bool
        :returns: The rows of the steps of the book.
        :rtype: StepRows
        """
        if new:
            await self.databases().open(self.book_id).change(lambda repos, _book_record: repos.recipes.add(recipe))
        else:
            await self.databases().open(self.book_id).change(lambda repos, _book_record: repos.recipes.update(recipe))
        return await self.sessions.read(self.book_id, StepRows.read)


@pytest.fixture
async def fx_book(
    fx_catalogue: CatalogueDatabaseFactory,
    fx_owner_id: AccountId,
    fx_databases: BookDatabasesFactory,
    fx_book_sessions: BookSessions,
) -> Book:
    """Store a book with one page.

    :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_owner_id: Committed account owning the book.
    :type fx_owner_id: AccountId
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :param fx_book_sessions: Sessions of the files of the books of the test.
    :type fx_book_sessions: BookSessions
    :returns: The book.
    :rtype: Book
    """
    book_id = await store_book(fx_catalogue(), make_record(), make_page(), owner=fx_owner_id, databases=fx_databases())
    return Book(book_id=book_id, databases=fx_databases, sessions=fx_book_sessions)


def three_steps(kind: RecipeKind = RecipeKind.TEXT) -> Recipe:
    """Build a recipe of the geometry stage with three deskew steps, which differ by their angle.

    :param kind: The kind of the recipe.
    :type kind: RecipeKind
    :returns: The recipe.
    :rtype: Recipe
    """
    steps = tuple(Step(processor_key=DESKEW.key, params={MAX_ANGLE: angle}) for angle in range(1, 4))
    return evolve(make_recipe(kind=kind), steps=steps)


class TestRecipeStepRows:
    """Tests for the rows SqlAlchemyRecipeRepository writes back for the steps of a recipe."""

    async def test_moving_a_step_rewrites_the_order_key_of_that_step_alone(self, fx_book: Book) -> None:
        """Verify the last step moved to the front gets a key before the first, and no other row changes.

        :param fx_book: The stored book.
        :type fx_book: Book
        """
        recipe = three_steps()
        first, second, third = recipe.steps
        before = await fx_book.store(recipe, new=True)
        after = await fx_book.store(evolve(recipe, steps=(third, first, second)))
        expect(before.changed(after) == (set(), {(recipe.id, third.step_id)}, set()))
        moved = after.places[recipe.id, third.step_id][0]
        expect(moved < after.places[recipe.id, first.step_id][0] < after.places[recipe.id, second.step_id][0])
        assert_expectations()

    async def test_a_changed_parameter_or_switch_rewrites_one_row(self, fx_book: Book) -> None:
        """Verify new parameters of a step rewrite the row of its type, and its switch the row of its place.

        :param fx_book: The stored book.
        :type fx_book: Book
        """
        recipe = three_steps()
        first, second, third = recipe.steps
        before = await fx_book.store(recipe, new=True)
        tuned = await fx_book.store(evolve(recipe, steps=(first, evolve(second, params={MAX_ANGLE: 9}), third)))
        switched = await fx_book.store(evolve(recipe, steps=(first, second, evolve(third, enabled=False))))
        expect(before.changed(tuned) == (set(), set(), {(recipe.id, second.step_id, DESKEW.key)}))
        expect(
            tuned.changed(switched) == (set(), {(recipe.id, third.step_id)}, {(recipe.id, second.step_id, DESKEW.key)})
        )
        assert_expectations()

    async def test_a_removed_step_keeps_its_row_marked_removed_and_comes_back_unmarked(self, fx_book: Book) -> None:
        """Verify a step out of its recipe loses its place and parameters but keeps its row, which a return reuses.

        :param fx_book: The stored book.
        :type fx_book: Book
        """
        recipe = three_steps()
        first, second, third = recipe.steps
        await fx_book.store(recipe, new=True)
        removed = await fx_book.store(evolve(recipe, steps=(first, third)))
        returned = await fx_book.store(evolve(recipe, steps=(first, third, second)))
        expect(removed.steps[second.step_id][1] is not None)
        expect((recipe.id, second.step_id) not in removed.places)
        expect((recipe.id, second.step_id, DESKEW.key) not in removed.params)
        expect(returned.steps[second.step_id] == (DESKEW.key, None))
        expect(
            removed.changed(returned)
            == ({second.step_id}, {(recipe.id, second.step_id)}, {(recipe.id, second.step_id, DESKEW.key)})
        )
        assert_expectations()

    async def test_a_step_another_recipe_holds_is_not_marked_removed(self, fx_book: Book) -> None:
        """Verify a step two recipes of a stage hold stays unmarked when one of them lets it go.

        :param fx_book: The stored book.
        :type fx_book: Book
        """
        text = three_steps()
        blank = evolve(make_recipe(kind=RecipeKind.BLANK, minutes=1), steps=(text.steps[0],))
        await fx_book.store(text, new=True)
        await fx_book.store(blank, new=True)
        rows = await fx_book.store(evolve(text, steps=text.steps[1:]))
        expect(rows.steps[text.steps[0].step_id][1] is None)
        expect((blank.id, text.steps[0].step_id) in rows.places)
        assert_expectations()
