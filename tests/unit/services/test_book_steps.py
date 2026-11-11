"""Tests for the finder of the steps of recipes whose plugin reads the whole book."""

from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.margins import NormalizeParam
from bookreviver.domain.recipes import Step
from bookreviver.services.book_steps import BookSteps
from tests.harness.builders.domain import make_recipe
from tests.harness.fakes.processing import FakeCatalogue
from tests.harness.fakes.processors import BOOK_SIZED_KEY, BookSizedProcessor, FakeProcessor

PLAIN_KEY: str = FakeProcessor.spec.key
MISSING_KEY: str = 'geometry.not-installed'
PAGE_WIDTH_PX: int = 500
PAGE_HEIGHT_PX: int = 700
LINE_HEIGHT_PX: float = 30.0
# Parameters of the step that reads the book with every field the book could give set by hand
ALL_GIVEN: dict[str, object] = {
    NormalizeParam.PAGE_WIDTH: PAGE_WIDTH_PX,
    NormalizeParam.PAGE_HEIGHT: PAGE_HEIGHT_PX,
    NormalizeParam.LINE_HEIGHT: LINE_HEIGHT_PX,
}


def finder() -> BookSteps:
    """Build the finder over a catalogue with a step that needs one page only and one that reads the book.

    :returns: The finder.
    :rtype: BookSteps
    """
    return BookSteps(FakeCatalogue([FakeProcessor(), BookSizedProcessor()]))


class TestOf:
    """Tests for BookSteps.of."""

    def test_only_a_step_whose_plugin_reads_the_book_has_the_half(self) -> None:
        """Verify a step of a plain plugin and one of a plugin that is not installed have no half that reads the book."""
        steps = finder()
        expect(steps.of(Step(processor_key=BOOK_SIZED_KEY)) is BookSizedProcessor.book_step)
        expect(steps.of(Step(processor_key=PLAIN_KEY)) is None)
        expect(steps.of(Step(processor_key=MISSING_KEY)) is None)
        assert_expectations()


class TestPosition:
    """Tests for BookSteps.position."""

    def test_the_first_step_that_reads_the_book_is_found(self) -> None:
        """Verify the place of the step among the steps, and none among steps of plain plugins."""
        steps = finder()
        plain, reading = Step(processor_key=PLAIN_KEY), Step(processor_key=BOOK_SIZED_KEY)
        expect(steps.position([plain, reading, reading]) == 1)
        expect(steps.position([plain]) is None)
        expect(steps.position([]) is None)
        assert_expectations()


class TestLeavesToBook:
    """Tests for BookSteps.leaves_to_book."""

    def test_a_field_left_to_the_book_makes_the_recipe_depend_on_the_book(self) -> None:
        """Verify a recipe leaves something to the book only while its step that reads it has a field unset."""
        steps = finder()
        recipe = make_recipe()
        left = evolve(recipe, steps=(Step(processor_key=PLAIN_KEY), Step(processor_key=BOOK_SIZED_KEY)))
        given = evolve(recipe, steps=(Step(processor_key=BOOK_SIZED_KEY, params=ALL_GIVEN),))
        expect(steps.leaves_to_book(left))
        expect(not steps.leaves_to_book(given))
        expect(not steps.leaves_to_book(evolve(recipe, steps=(Step(processor_key=PLAIN_KEY),))))
        assert_expectations()

    def test_a_step_the_run_stops_before_is_not_asked(self) -> None:
        """Verify a run through an earlier step leaves nothing to the book, and a step that is off does not either."""
        steps = finder()
        recipe = evolve(make_recipe(), steps=(Step(processor_key=PLAIN_KEY), Step(processor_key=BOOK_SIZED_KEY)))
        off = evolve(recipe, steps=(recipe.steps[0], evolve(recipe.steps[1], enabled=False)))
        expect(not steps.leaves_to_book(recipe, through_step=0))
        expect(steps.leaves_to_book(recipe, through_step=1))
        expect(not steps.leaves_to_book(off))
        assert_expectations()
