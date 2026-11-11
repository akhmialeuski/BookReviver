"""Tests for what the recipes of a book tell of a step without reading the book."""

from delayed_assert import assert_expectations, expect

from bookreviver.domain.recipes import Step
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipes import DefaultRecipes, RecipeBook
from tests.harness.fakes.processing import FakeCatalogue
from tests.harness.fakes.processors import AutoSplitProcessor, FakeProcessor

MISSING_KEY: str = 'split.not-installed'


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
