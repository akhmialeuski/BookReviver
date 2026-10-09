"""Tests for the order of the steps of a recipe: the issues a step out of its place makes, and the refusal of one."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import OrderMode, OrderRuleKind, RecipeKind, Stage
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.values import ProfileDraft, RecipeDraft, Step
from bookreviver.services.recipe_order import RecipeOrder
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.processors import (
    FIRST_KEY,
    OPENING_KEY,
    OPENING_REASON,
    SECOND_KEY,
    SECOND_REASON,
    THIRD_KEY,
    THIRD_REASON,
    FirstProcessor,
    OpeningProcessor,
    SecondProcessor,
    ThirdProcessor,
)

if TYPE_CHECKING:
    from tests.helpers.processing import ProcessingKit

UNKNOWN_KEY: str = 'geometry.gone'
DRAFT_NAME: str = 'Ordered'


@pytest.fixture
def fx_order() -> RecipeOrder:
    """Build the finder over a catalogue of the processors that declare a place.

    :returns: The finder of steps that are out of their place.
    :rtype: RecipeOrder
    """
    return RecipeOrder(FakeCatalogue([OpeningProcessor(), FirstProcessor(), SecondProcessor(), ThirdProcessor()]))


def steps_of(*keys: str, enabled: bool = True) -> list[Step]:
    """Build steps of the processors in the given order.

    :param keys: Keys of the processors.
    :type keys: str
    :param enabled: Whether the steps are on.
    :type enabled: bool
    :returns: The steps.
    :rtype: list[Step]
    """
    return [Step(processor_key=key, enabled=enabled) for key in keys]


class TestIssues:
    """Tests for RecipeOrder.issues."""

    def test_steps_in_their_usual_order_make_no_issue(self, fx_order: RecipeOrder) -> None:
        """Verify a recipe that keeps every declared place is clean.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        steps = steps_of(OPENING_KEY, FIRST_KEY, SECOND_KEY, THIRD_KEY)
        assert fx_order.issues(steps) == ()

    def test_a_step_before_the_one_it_usually_follows_is_an_issue_of_the_usual_kind(
        self, fx_order: RecipeOrder
    ) -> None:
        """Verify the step that declared the rule is the one named, with the reason and the step it is compared with.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        steps = steps_of(SECOND_KEY, FIRST_KEY)
        (issue,) = fx_order.issues(steps)
        expect(issue.step_id == steps[0].step_id)
        expect(issue.other_step_id == steps[1].step_id)
        expect((issue.processor_key, issue.other_key) == (SECOND_KEY, FIRST_KEY))
        expect((issue.kind, issue.reason) == (OrderRuleKind.USUAL, SECOND_REASON))
        assert_expectations()

    def test_a_step_after_the_one_it_usually_precedes_is_an_issue_of_the_step_that_declared_it(
        self, fx_order: RecipeOrder
    ) -> None:
        """Verify a rule declared with ``before`` marks the step that declared it.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        steps = steps_of(FIRST_KEY, OPENING_KEY)
        (issue,) = fx_order.issues(steps)
        expect((issue.step_id, issue.kind, issue.reason) == (steps[1].step_id, OrderRuleKind.USUAL, OPENING_REASON))
        assert_expectations()

    def test_a_step_before_the_one_it_requires_is_an_issue_of_the_required_kind(self, fx_order: RecipeOrder) -> None:
        """Verify a rule declared with ``requires_after`` is of the required kind.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        (issue,) = fx_order.issues(steps_of(THIRD_KEY, SECOND_KEY))
        assert (issue.kind, issue.reason) == (OrderRuleKind.REQUIRED, THIRD_REASON)

    def test_a_step_that_is_off_counts_by_its_place_too(self, fx_order: RecipeOrder) -> None:
        """Verify the order does not depend on the switch, so switching a step on never makes a place wrong.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        assert len(fx_order.issues(steps_of(SECOND_KEY, FIRST_KEY, enabled=False))) == 1

    def test_a_rule_whose_other_processor_is_not_in_the_recipe_is_not_broken(self, fx_order: RecipeOrder) -> None:
        """Verify a step that stands alone, or beside a processor it names no rule for, is in place.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        assert fx_order.issues(steps_of(THIRD_KEY, FIRST_KEY)) == ()

    def test_a_processor_the_catalogue_does_not_offer_has_no_rules(self, fx_order: RecipeOrder) -> None:
        """Verify a step of a processor that was removed from the application is passed over.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        issues = fx_order.issues(steps_of(UNKNOWN_KEY, SECOND_KEY, UNKNOWN_KEY, FIRST_KEY))
        assert [issue.processor_key for issue in issues] == [SECOND_KEY]

    def test_two_steps_of_one_processor_name_the_first_step_that_breaks_the_rule(self, fx_order: RecipeOrder) -> None:
        """Verify a recipe that holds a processor twice is checked against each of its steps.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        steps = steps_of(FIRST_KEY, SECOND_KEY, FIRST_KEY, FIRST_KEY)
        (issue,) = fx_order.issues(steps)
        expect(issue.step_id == steps[1].step_id)
        expect(issue.other_step_id == steps[2].step_id)
        assert_expectations()

    def test_two_steps_of_the_processor_that_declared_the_rule_are_each_checked(self, fx_order: RecipeOrder) -> None:
        """Verify the second of two steps of the same processor is marked when it alone stands too early.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        steps = steps_of(FIRST_KEY, SECOND_KEY, SECOND_KEY)
        assert fx_order.issues(steps) == ()
        late = steps_of(SECOND_KEY, FIRST_KEY, SECOND_KEY)
        assert [issue.step_id for issue in fx_order.issues(late)] == [late[0].step_id]


class TestEnforce:
    """Tests for RecipeOrder.enforce."""

    def test_a_required_place_is_refused_in_the_usual_order_with_the_reason(self, fx_order: RecipeOrder) -> None:
        """Verify the refusal carries the reason of the rule.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        with pytest.raises(InvalidParametersError, match=THIRD_REASON):
            fx_order.enforce(steps_of(THIRD_KEY, SECOND_KEY), OrderMode.USUAL)

    def test_a_required_place_is_allowed_in_the_free_order(self, fx_order: RecipeOrder) -> None:
        """Verify the free order lets the step stand, the issue staying for the interface to warn of.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        steps = steps_of(THIRD_KEY, SECOND_KEY)
        fx_order.enforce(steps, OrderMode.FREE)
        assert [issue.kind for issue in fx_order.issues(steps)] == [OrderRuleKind.REQUIRED]

    def test_a_step_off_its_usual_place_is_never_refused(self, fx_order: RecipeOrder) -> None:
        """Verify a place that is only the usual one is allowed in the usual order.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        fx_order.enforce(steps_of(SECOND_KEY, FIRST_KEY), OrderMode.USUAL)

    def test_the_refusal_names_each_reason_once(self, fx_order: RecipeOrder) -> None:
        """Verify two steps that break the same rule do not repeat its reason.

        :param fx_order: Finder over the processors of the test.
        :type fx_order: RecipeOrder
        """
        with pytest.raises(InvalidParametersError) as refused:
            fx_order.enforce(steps_of(THIRD_KEY, THIRD_KEY, SECOND_KEY), OrderMode.USUAL)
        assert str(refused.value) == THIRD_REASON


def draft_of(*keys: str, order: OrderMode = OrderMode.USUAL) -> RecipeDraft:
    """Build a draft of steps of the processors in the given order.

    :param keys: Keys of the processors.
    :type keys: str
    :param order: The order to keep.
    :type order: OrderMode
    :returns: The draft.
    :rtype: RecipeDraft
    """
    return RecipeDraft(steps=steps_of(*keys), order=order)


def profile_draft_of(*keys: str, order: OrderMode = OrderMode.USUAL) -> ProfileDraft:
    """Build a draft of a profile of steps of the processors in the given order.

    :param keys: Keys of the processors.
    :type keys: str
    :param order: The order to keep.
    :type order: OrderMode
    :returns: The draft.
    :rtype: ProfileDraft
    """
    return ProfileDraft(name=DRAFT_NAME, steps=steps_of(*keys), order=order)


@pytest.mark.anyio
class TestSavePaths:
    """Tests that every way of storing steps keeps the order, since the check is part of the service."""

    async def test_the_active_recipe_is_refused_a_required_place_and_saved_in_the_free_order(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify ProcessingService.save_recipe refuses in the usual order and stores the steps in the free one.

        :param fx_ordered_kit: The kit over the processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        with pytest.raises(InvalidParametersError, match=THIRD_REASON):
            await fx_ordered_kit.edit_recipe(actor, project, Stage.GEOMETRY, draft_of(THIRD_KEY, SECOND_KEY))
        saved = await fx_ordered_kit.edit_recipe(
            actor, project, Stage.GEOMETRY, draft_of(THIRD_KEY, SECOND_KEY, order=OrderMode.FREE)
        )
        assert [step.processor_key for step in saved.steps] == [THIRD_KEY, SECOND_KEY]

    async def test_the_recipe_of_another_kind_is_refused_and_saved_by_the_same_rule(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify ProcessingService.save_recipe keeps the order for the recipe of every kind.

        :param fx_ordered_kit: The kit over the processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        wrong = draft_of(THIRD_KEY, SECOND_KEY)
        with pytest.raises(InvalidParametersError, match=THIRD_REASON):
            await fx_ordered_kit.edit_recipe(actor, project, Stage.GEOMETRY, wrong, RecipeKind.BLANK)
        saved = await fx_ordered_kit.edit_recipe(
            actor, project, Stage.GEOMETRY, draft_of(FIRST_KEY, SECOND_KEY), RecipeKind.BLANK
        )
        assert [step.processor_key for step in saved.steps] == [FIRST_KEY, SECOND_KEY]

    async def test_a_profile_is_refused_a_required_place_unless_the_order_is_free(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify RecipeProfiles.save keeps the order, and applying a profile saved in the free order is allowed.

        :param fx_ordered_kit: The kit over the processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        profiles = fx_ordered_kit.profiles()
        with pytest.raises(InvalidParametersError, match=THIRD_REASON):
            await profiles.save(actor, Stage.GEOMETRY, profile_draft_of(THIRD_KEY, SECOND_KEY))
        profile = await profiles.save(
            actor, Stage.GEOMETRY, profile_draft_of(THIRD_KEY, SECOND_KEY, order=OrderMode.FREE)
        )
        applied = await profiles.apply(actor, project.id, profile.id, kind=RecipeKind.TEXT)
        assert [step.processor_key for step in applied.recipe.steps] == [THIRD_KEY, SECOND_KEY]
