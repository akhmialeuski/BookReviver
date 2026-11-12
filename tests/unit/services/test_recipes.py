"""Tests for what the recipes of a book tell of a step without reading the book."""

from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.recipes import Step
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipes import DefaultRecipes, RecipeBook
from tests.harness.builders.domain import make_recipe
from tests.harness.fakes.processing import FakeCatalogue
from tests.harness.fakes.processors import BOOK_SIZED_KEY, AutoSplitProcessor, BookSizedProcessor, FakeProcessor

MISSING_KEY: str = 'split.not-installed'


class TestLeavesToBook:
    """Tests for RecipeBook.leaves_to_book."""

    def test_a_recipe_leaves_to_the_book_only_through_a_step_the_run_reaches(self) -> None:
        """Verify the recipes answer whether a run depends on the book, so a plan asks them and no finder they hold.

        A run that stops before the step that reads the book leaves nothing to it, so its pages stay fresh when another
        page changes.
        """
        catalogue = FakeCatalogue([FakeProcessor(), BookSizedProcessor()])
        recipes = RecipeBook(processors=catalogue, defaults=DefaultRecipes({}), order=RecipeOrder(catalogue))
        recipe = evolve(
            make_recipe(), steps=(Step(processor_key=FakeProcessor.spec.key), Step(processor_key=BOOK_SIZED_KEY))
        )
        expect(recipes.leaves_to_book(recipe))
        expect(not recipes.leaves_to_book(recipe, through_step=0))
        assert_expectations()


class TestBookStages:
    """Tests for RecipeBook.book_stages."""

    def test_only_the_stage_of_an_installed_plugin_that_reads_the_book_is_named(self) -> None:
        """Verify a measure asks for the recipes of the stages a plugin that reads the book belongs to, and no other."""
        reading = FakeCatalogue([FakeProcessor(), BookSizedProcessor()])
        plain = FakeCatalogue([FakeProcessor()])
        expect(
            RecipeBook(processors=reading, defaults=DefaultRecipes({}), order=RecipeOrder(reading)).book_stages()
            == [BookSizedProcessor.spec.stage]
        )
        expect(RecipeBook(processors=plain, defaults=DefaultRecipes({}), order=RecipeOrder(plain)).book_stages() == [])
        assert_expectations()


class TestBookStepOf:
    """Tests for RecipeBook.book_step_of."""

    def test_the_first_step_that_reads_the_book_is_given_with_its_half(self) -> None:
        """Verify a measure finds the place of the step it writes into and the half that measures, and none without."""
        catalogue = FakeCatalogue([FakeProcessor(), BookSizedProcessor()])
        recipes = RecipeBook(processors=catalogue, defaults=DefaultRecipes({}), order=RecipeOrder(catalogue))
        plain, reading = Step(processor_key=FakeProcessor.spec.key), Step(processor_key=BOOK_SIZED_KEY)
        expect(
            recipes.book_step_of(evolve(make_recipe(), steps=(plain, reading, reading)))
            == (1, BookSizedProcessor.book_step)
        )
        expect(recipes.book_step_of(evolve(make_recipe(), steps=(plain,))) is None)
        assert_expectations()


class TestUnattended:
    """Tests for RecipeBook.unattended."""

    def test_only_an_installed_step_that_says_so_runs_without_the_user(self) -> None:
        """Verify a step is unattended by the spec of its processor, and never when the processor is not installed."""
        catalogue = FakeCatalogue([FakeProcessor(), AutoSplitProcessor()])
        recipes = RecipeBook(processors=catalogue, defaults=DefaultRecipes({}), order=RecipeOrder(catalogue))
        expect(recipes.unattended(Step(processor_key=AutoSplitProcessor.spec.key)))
        expect(not recipes.unattended(Step(processor_key=FakeProcessor.spec.key)))
        expect(not recipes.unattended(Step(processor_key=MISSING_KEY)))
        assert_expectations()
