"""Tests for the rules of a stage: what a condition matches, and what a rule refuses to be built from."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import PageKind, RuleCondition, Stage
from bookreviver.domain.ids import RecipeId
from bookreviver.domain.values import StageRun
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_pinned_stage,
    make_project,
    make_recipe,
    make_recipe_rule,
    new_account_id,
)

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageStage, RecipeRule
    from bookreviver.domain.ids import ProjectId

PROJECT_ID: ProjectId = make_project(owner_id=new_account_id()).id
RECIPE = make_recipe(project_id=PROJECT_ID)
FIRST_POSITION: int = 1
SECOND_POSITION: int = 2
GROUP: str = 'Engravings'


class TestRuleCondition:
    """Tests for the closed set of conditions."""

    def test_every_condition_has_a_label(self) -> None:
        """Verify no condition is left without the name the interface shows."""
        assert all(condition.label for condition in RuleCondition)

    def test_conditions_on_the_kind_name_the_kinds_they_match(self) -> None:
        """Verify the plates, the covers and the blank pages are groups of kinds, and the others test no kind."""
        assert {condition: condition.kinds for condition in RuleCondition} == {
            RuleCondition.PLATES: {PageKind.PLATE, PageKind.FRONTISPIECE},
            RuleCondition.COVERS: {PageKind.COVER, PageKind.BACK_COVER},
            RuleCondition.BLANKS: {PageKind.BLANK},
            RuleCondition.ILLUSTRATED: frozenset(),
            RuleCondition.ODD: frozenset(),
            RuleCondition.EVEN: frozenset(),
            RuleCondition.GROUP: frozenset(),
        }


class TestRecipeRule:
    """Tests for RecipeRule."""

    @pytest.mark.parametrize(
        ('condition', 'kind', 'expected'),
        [
            (RuleCondition.PLATES, PageKind.PLATE, True),
            (RuleCondition.PLATES, PageKind.FRONTISPIECE, True),
            (RuleCondition.PLATES, PageKind.TEXT, False),
            (RuleCondition.COVERS, PageKind.BACK_COVER, True),
            (RuleCondition.COVERS, PageKind.ENDPAPER, False),
            (RuleCondition.BLANKS, PageKind.BLANK, True),
            (RuleCondition.BLANKS, PageKind.OTHER, False),
        ],
    )
    def test_a_condition_on_the_kind_matches_the_pages_of_its_kinds(
        self, condition: RuleCondition, kind: PageKind, *, expected: bool
    ) -> None:
        """Verify a condition on the kind matches a page of one of its kinds and no other.

        :param condition: Condition of the rule.
        :type condition: RuleCondition
        :param kind: Kind of the page.
        :type kind: PageKind
        :param expected: Whether the rule matches the page.
        :type expected: bool
        """
        rule = make_recipe_rule(recipe=RECIPE, condition=condition)
        assert rule.matches(make_page(project_id=PROJECT_ID, kind=kind), FIRST_POSITION) is expected

    @pytest.mark.parametrize(
        ('condition', 'position', 'expected'),
        [
            (RuleCondition.ODD, FIRST_POSITION, True),
            (RuleCondition.ODD, SECOND_POSITION, False),
            (RuleCondition.EVEN, FIRST_POSITION, False),
            (RuleCondition.EVEN, SECOND_POSITION, True),
        ],
    )
    def test_parity_is_read_from_the_place_of_the_page_in_the_book(
        self, condition: RuleCondition, position: int, *, expected: bool
    ) -> None:
        """Verify odd pages are the first, third and so on, counted from 1.

        :param condition: Condition of the rule.
        :type condition: RuleCondition
        :param position: Place of the page in the book counted from 1.
        :type position: int
        :param expected: Whether the rule matches the page.
        :type expected: bool
        """
        rule = make_recipe_rule(recipe=RECIPE, condition=condition)
        assert rule.matches(make_page(project_id=PROJECT_ID), position) is expected

    def test_a_manual_group_matches_the_pages_with_its_label_only(self) -> None:
        """Verify a page in the group matches, and a page in another group or in none does not."""
        rule = make_recipe_rule(recipe=RECIPE, condition=RuleCondition.GROUP, group_label=GROUP)
        members = (
            make_page(project_id=PROJECT_ID, group_label=GROUP),
            make_page(project_id=PROJECT_ID, group_label='Maps'),
            make_page(project_id=PROJECT_ID),
        )
        assert [rule.matches(page, FIRST_POSITION) for page in members] == [True, False, False]

    @pytest.mark.parametrize('kind', list(PageKind))
    def test_illustrations_match_no_page_until_the_layout_stage_finds_them(self, kind: PageKind) -> None:
        """Verify the condition on illustrations is declared and matches nothing, whatever the page.

        :param kind: Kind of the page.
        :type kind: PageKind
        """
        rule = make_recipe_rule(recipe=RECIPE, condition=RuleCondition.ILLUSTRATED)
        assert not rule.matches(make_page(project_id=PROJECT_ID, kind=kind), FIRST_POSITION)

    def test_a_manual_group_needs_a_label(self) -> None:
        """Reject a rule on a manual group that names no group."""
        with pytest.raises(ValueError, match='needs the label'):
            make_recipe_rule(recipe=RECIPE, condition=RuleCondition.GROUP)

    @pytest.mark.parametrize('condition', [c for c in RuleCondition if c is not RuleCondition.GROUP])
    def test_only_a_manual_group_takes_a_label(self, condition: RuleCondition) -> None:
        """Reject a label on any other condition.

        :param condition: Condition of the rule.
        :type condition: RuleCondition
        """
        with pytest.raises(ValueError, match='takes no group label'):
            make_recipe_rule(recipe=RECIPE, condition=condition, group_label=GROUP)

    def test_the_order_is_not_negative(self) -> None:
        """Reject a rule placed before the first."""
        with pytest.raises(ValueError, match='order'):
            evolve(make_recipe_rule(recipe=RECIPE), order=-1)

    def test_a_rule_keeps_the_project_and_stage_of_its_recipe(self) -> None:
        """Verify the builder gives a rule of the stage of the recipe it names."""
        rule: RecipeRule = make_recipe_rule(recipe=RECIPE)
        assert (rule.project_id, rule.stage, rule.recipe_id) == (PROJECT_ID, Stage.GEOMETRY, RECIPE.id)


class TestPageStagePin:
    """Tests for the pin of a page stage and of a run."""

    def test_a_pinned_record_names_the_pinned_recipe(self) -> None:
        """Verify the pin holds the recipe of the record."""
        recipe_id = RecipeId(RECIPE.id)
        record: PageStage = make_pinned_stage(page_id=make_page(project_id=PROJECT_ID).id, recipe_id=recipe_id)
        assert record.pinned_recipe_id == recipe_id

    def test_a_pin_without_a_recipe_holds_nothing(self) -> None:
        """Verify a pin whose recipe was deleted pins no recipe."""
        record = make_pinned_stage(page_id=make_page(project_id=PROJECT_ID).id, recipe_id=None)
        assert record.pinned_recipe_id is None

    def test_a_record_that_is_not_pinned_pins_nothing(self) -> None:
        """Verify the recipe a page was processed by is not pinned unless the user pinned it."""
        record = make_page_stage(page_id=make_page(project_id=PROJECT_ID).id, recipe_id=RECIPE.id)
        assert record.pinned_recipe_id is None

    def test_a_run_pins_only_a_recipe_it_names(self) -> None:
        """Reject a run that pins and chooses the recipes itself."""
        with pytest.raises(ValueError, match='names the recipe'):
            StageRun(stage=Stage.GEOMETRY, pin=True)

    def test_the_pin_of_a_run_survives_the_job_parameters(self) -> None:
        """Verify a run read back from the parameters of its job still pins."""
        run = StageRun(stage=Stage.GEOMETRY, recipe_id=RECIPE.id, pin=True)
        assert StageRun.from_map(run.to_map()) == run
