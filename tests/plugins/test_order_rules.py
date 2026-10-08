"""Tests for the order of steps the built-in processors ask for in their specs.

The specs are read through the entry points as the application reads them, so a rule that names a processor under a
wrong key is found here. The processors that need OpenCV are checked where the optional group ``cv`` is installed.
"""

from graphlib import TopologicalSorter
from typing import TYPE_CHECKING

import pytest

from bookreviver.app.plugins import EntryPointCatalog
from bookreviver.domain.enums import WorkerPool
from bookreviver.domain.values import Step
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipes import DefaultRecipes
from tests.helpers.samples import CV_MISSING

if TYPE_CHECKING:
    from bookreviver.domain.values import ProcessorSpec

MARGINS_KEY: str = 'geometry.normalize'
CROP_KEY: str = 'geometry.crop'
DESPECKLE_KEY: str = 'cleanup.despeckle'
BINARIZE_KEY: str = 'cleanup.binarize'
THICKNESS_KEY: str = 'cleanup.thickness'


@pytest.fixture
def fx_catalogue() -> EntryPointCatalog:
    """Load the built-in processors, which need OpenCV for the most part.

    :returns: The catalogue of the processors the project registers.
    :rtype: EntryPointCatalog
    """
    pytest.importorskip('cv2', reason=CV_MISSING)
    return EntryPointCatalog(pools=set(WorkerPool))


def spec_of(catalogue: EntryPointCatalog, key: str) -> ProcessorSpec:
    """Find the spec of a processor.

    :param catalogue: The catalogue of the processors.
    :type catalogue: EntryPointCatalog
    :param key: Key of the processor.
    :type key: str
    :returns: Its spec.
    :rtype: ProcessorSpec
    """
    return catalogue.get(key).spec


class TestOrderRules:
    """Tests for the rules in the specs of the built-in processors."""

    def test_every_rule_names_another_processor_of_the_same_stage(self, fx_catalogue: EntryPointCatalog) -> None:
        """Verify a rule never names a processor that is not offered, itself, or one of another stage.

        :param fx_catalogue: The catalogue of the built-in processors.
        :type fx_catalogue: EntryPointCatalog
        """
        stages = {spec.key: spec.stage for spec in fx_catalogue.specs()}
        wrong = [
            f'{spec.key} names {rule.processor_key}'
            for spec in fx_catalogue.specs()
            for rule in (*spec.after, *spec.before, *spec.requires_after)
            if rule.processor_key == spec.key or stages.get(rule.processor_key) is not spec.stage
        ]
        assert wrong == []

    def test_every_rule_has_a_reason_the_reader_can_use(self, fx_catalogue: EntryPointCatalog) -> None:
        """Verify the reason is a sentence, which the interface shows as it is.

        :param fx_catalogue: The catalogue of the built-in processors.
        :type fx_catalogue: EntryPointCatalog
        """
        reasons = [
            rule.reason for spec in fx_catalogue.specs() for rule in (*spec.after, *spec.before, *spec.requires_after)
        ]
        assert reasons
        assert all(reason.endswith('.') and ' ' in reason and '_' not in reason for reason in reasons)

    def test_the_rules_can_all_be_met_at_once(self, fx_catalogue: EntryPointCatalog) -> None:
        """Verify no processor has to come before another that has to come before it, which no order could satisfy.

        :param fx_catalogue: The catalogue of the built-in processors.
        :type fx_catalogue: EntryPointCatalog
        """
        later: dict[str, set[str]] = {spec.key: set() for spec in fx_catalogue.specs()}
        for spec in fx_catalogue.specs():
            for rule in (*spec.after, *spec.requires_after):
                later[spec.key].add(rule.processor_key)
            for rule in spec.before:
                later[rule.processor_key].add(spec.key)
        # A cycle raises graphlib.CycleError
        assert set(TopologicalSorter(later).static_order()) == set(later)

    def test_every_recipe_a_stage_starts_with_is_in_its_usual_order(self, fx_catalogue: EntryPointCatalog) -> None:
        """Verify the templates a new book gets carry no mark of a step out of its place.

        :param fx_catalogue: The catalogue of the built-in processors.
        :type fx_catalogue: EntryPointCatalog
        """
        order = RecipeOrder(fx_catalogue)
        marked = [
            f'{stage}: {kind}'
            for stage, templates in DefaultRecipes.TEMPLATES.items()
            for kind, template in templates.items()
            if order.issues([Step(processor_key=key) for key in template.processor_keys])
        ]
        assert marked == []

    def test_margins_must_follow_select_content_and_despeckle_and_thickness_must_follow_binarization(
        self, fx_catalogue: EntryPointCatalog
    ) -> None:
        """Verify the steps that lose their meaning without the one before them require their place.

        :param fx_catalogue: The catalogue of the built-in processors.
        :type fx_catalogue: EntryPointCatalog
        """
        required = {
            key: [rule.processor_key for rule in spec_of(fx_catalogue, key).requires_after]
            for key in (MARGINS_KEY, DESPECKLE_KEY, THICKNESS_KEY)
        }
        assert required == {MARGINS_KEY: [CROP_KEY], DESPECKLE_KEY: [BINARIZE_KEY], THICKNESS_KEY: [BINARIZE_KEY]}
