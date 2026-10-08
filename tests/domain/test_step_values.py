"""Tests for the values a step has for a part of the pages, and for the order they are laid over the recipe in."""

from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import pytest

from bookreviver.domain.enums import ValueScope
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.domain.step_values import StepValueLayers, ValueTarget, pages_changing_side
from tests.helpers.builders import make_step_values

if TYPE_CHECKING:
    from bookreviver.domain.step_values import StepValues

PROJECT_ID: ProjectId = ProjectId(uuid4())
GROUP: str = 'Index'
RECIPE: dict[str, int] = {'strength': 1, 'depth': 10}


class Place(NamedTuple):
    """A page of the book as the layers read it.

    :ivar position: Place of the page in the book counted from 1.
    :ivar group_label: Label of the group of the page, or empty.
    """

    position: int
    group_label: str = ''


def values(scope: ValueScope, strength: int, *, group_label: str = '') -> StepValues:
    """Build the values of the strength for a part of the pages.

    :param scope: The odd pages, the even pages or a group.
    :type scope: ValueScope
    :param strength: The strength the part uses.
    :type strength: int
    :param group_label: Label of the group, for the scope of a group.
    :type group_label: str
    :returns: The values.
    :rtype: StepValues
    """
    return make_step_values(project_id=PROJECT_ID, scope=scope, group_label=group_label, params={'strength': strength})


class TestValueTarget:
    """Tests for the pages a value is for, which name what their scope needs and nothing else."""

    @pytest.mark.parametrize(
        'target',
        [
            {'scope': ValueScope.PAGES, 'page_ids': (PageId(uuid4()),)},
            {'scope': ValueScope.ODD},
            {'scope': ValueScope.EVEN},
            {'scope': ValueScope.GROUP, 'group_label': GROUP},
        ],
        ids=['pages', 'odd', 'even', 'group'],
    )
    def test_a_target_that_names_what_its_scope_needs_is_built(self, target: dict[str, Any]) -> None:
        """Verify each scope is built from exactly the pages or the label it needs.

        :param target: Arguments of the target.
        :type target: dict[str, Any]
        """
        assert ValueTarget(**target).scope is target['scope']

    @pytest.mark.parametrize(
        'target',
        [
            {'scope': ValueScope.PAGES},
            {'scope': ValueScope.ODD, 'page_ids': (PageId(uuid4()),)},
            {'scope': ValueScope.GROUP},
            {'scope': ValueScope.EVEN, 'group_label': GROUP},
        ],
        ids=['pages-without-pages', 'side-with-pages', 'group-without-label', 'side-with-label'],
    )
    def test_a_target_that_lacks_or_adds_something_is_refused(self, target: dict[str, Any]) -> None:
        """Verify missing pages, missing label, and pages or a label the scope does not take are refused.

        :param target: Arguments of the target.
        :type target: dict[str, Any]
        """
        with pytest.raises(ValueError, match='names'):
            ValueTarget(**target)


class TestStepValues:
    """Tests for the values of one part of the pages."""

    def test_values_for_pages_are_not_kept_once_for_the_step(self) -> None:
        """Verify the values of pages belong to the pages and are refused as values of a part."""
        with pytest.raises(ValueError, match='kept by the pages'):
            make_step_values(project_id=PROJECT_ID, scope=ValueScope.PAGES)

    @pytest.mark.parametrize(
        ('scope', 'label'), [(ValueScope.GROUP, ''), (ValueScope.ODD, GROUP)], ids=['group-no-label', 'side-label']
    )
    def test_a_label_goes_with_a_group_and_only_with_it(self, scope: ValueScope, label: str) -> None:
        """Verify a group needs its label, and the odd and the even pages have none.

        :param scope: Scope under test.
        :type scope: ValueScope
        :param label: Label under test.
        :type label: str
        """
        with pytest.raises(ValueError, match='group'):
            make_step_values(project_id=PROJECT_ID, scope=scope, group_label=label)

    @pytest.mark.parametrize(
        ('scope', 'place', 'covered'),
        [
            (ValueScope.ODD, Place(1), True),
            (ValueScope.ODD, Place(2), False),
            (ValueScope.EVEN, Place(2), True),
            (ValueScope.EVEN, Place(3), False),
            (ValueScope.GROUP, Place(5, GROUP), True),
            (ValueScope.GROUP, Place(5, 'Plates'), False),
            (ValueScope.GROUP, Place(5), False),
        ],
    )
    def test_the_place_and_the_group_of_a_page_tell_whether_the_values_are_its(
        self, scope: ValueScope, place: Place, *, covered: bool
    ) -> None:
        """Verify a page is in a side by the parity of its place and in a group by its label, which is never empty.

        :param scope: Scope of the values under test.
        :type scope: ValueScope
        :param place: The page.
        :type place: Place
        :param covered: Whether the values are the page's.
        :type covered: bool
        """
        part = values(scope, 2, group_label=GROUP if scope is ValueScope.GROUP else '')
        assert part.covers(group_label=place.group_label, position=place.position) is covered

    def test_values_with_no_field_are_empty(self) -> None:
        """Verify values are empty while they change no field, which is when they are no longer stored."""
        assert make_step_values(project_id=PROJECT_ID, params={}).is_empty


class TestStepValueLayers:
    """Tests for the order the values of the parts of the pages are laid over the recipe in."""

    layers = StepValueLayers(
        [
            values(ValueScope.EVEN, 2),
            values(ValueScope.ODD, 5),
            values(ValueScope.GROUP, 3, group_label=GROUP),
        ]
    )

    @pytest.mark.parametrize(
        ('place', 'own', 'strength'),
        [
            (Place(1), None, 5),
            (Place(2), None, 2),
            (Place(2, GROUP), None, 3),
            (Place(3, GROUP), None, 3),
            (Place(2, GROUP), {'strength': 4}, 4),
            (Place(2), {'strength': 4}, 4),
        ],
        ids=['odd', 'even', 'group-over-side', 'group-over-odd', 'page-over-group', 'page-over-side'],
    )
    def test_a_page_then_a_group_then_a_side_then_the_recipe(
        self, place: Place, own: dict[str, int] | None, strength: int
    ) -> None:
        """Verify the strongest part that has a value for the field gives it to the page.

        :param place: The page.
        :type place: Place
        :param own: The fields the page changes for itself.
        :type own: dict[str, int] | None
        :param strength: The strength the page runs with.
        :type strength: int
        """
        laid = self.layers.lay_over(RECIPE, group_label=place.group_label, position=place.position, own=own)
        assert laid['strength'] == strength

    def test_the_fields_no_part_changes_stay_the_recipes_and_the_recipe_is_not_mutated(self) -> None:
        """Verify a field a part does not change is the recipe's, and the recipe's parameters are left as they are."""
        recipe = dict(RECIPE)
        laid = self.layers.lay_over(recipe, group_label=GROUP, position=1)
        assert (laid, recipe) == ({'strength': 3, 'depth': 10}, RECIPE)

    def test_a_book_with_no_value_for_a_part_runs_with_the_recipe(self) -> None:
        """Verify no part of the pages and no page value leave the parameters of the recipe as they are."""
        assert StepValueLayers().lay_over(RECIPE, group_label=GROUP, position=2) == RECIPE


class TestPagesChangingSide:
    """Tests for the pages a change of the places of the pages turns over to the other side of the book."""

    FIRST: PageId = PageId(uuid4())
    SECOND: PageId = PageId(uuid4())
    THIRD: PageId = PageId(uuid4())
    FOURTH: PageId = PageId(uuid4())
    ADDED: PageId = PageId(uuid4())

    @pytest.mark.parametrize(
        ('before', 'after', 'turned'),
        [
            ((FIRST, SECOND, THIRD, FOURTH), (FIRST, SECOND, THIRD, FOURTH), ()),
            ((FIRST, SECOND, THIRD, FOURTH), (SECOND, FIRST, THIRD, FOURTH), (SECOND, FIRST)),
            ((FIRST, SECOND, THIRD, FOURTH), (SECOND, THIRD, FIRST, FOURTH), (SECOND, THIRD)),
            ((FIRST, SECOND, THIRD, FOURTH), (SECOND, THIRD, FOURTH), (SECOND, THIRD, FOURTH)),
            ((FIRST, SECOND, THIRD, FOURTH), (FIRST, SECOND, FOURTH), (FOURTH,)),
            ((FIRST, SECOND, THIRD), (FIRST, ADDED, SECOND, THIRD), (SECOND, THIRD)),
            ((FIRST, SECOND, THIRD), (FIRST, SECOND, THIRD, ADDED), ()),
        ],
        ids=['same', 'swap', 'move-over-two-pages', 'delete-first', 'delete-third', 'insert-second', 'append'],
    )
    def test_pages_standing_an_odd_number_of_places_away_turn_over(
        self, before: tuple[PageId, ...], after: tuple[PageId, ...], turned: tuple[PageId, ...]
    ) -> None:
        """Verify a page that is in both orders and moved by an odd number of places is picked, and no other page.

        :param before: The pages in order before the change.
        :type before: tuple[PageId, ...]
        :param after: The pages in order after the change.
        :type after: tuple[PageId, ...]
        :param turned: The pages that turned over, in the order they have after the change.
        :type turned: tuple[PageId, ...]
        """
        assert pages_changing_side(before, after) == list(turned)
