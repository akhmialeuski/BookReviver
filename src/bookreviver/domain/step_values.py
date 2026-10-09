"""The values a setting of a step has for a part of the pages, and the order they are laid over the recipe in.

Every field of the parameters of a step has the value of its recipe, and a part of the pages may have a value of its
own: one page, the pages the user selected, the odd pages, the even pages, or the pages of a group. A page takes the
field from the strongest part that has a value for it, a page before a group, a group before a side of the book, and a
side before the recipe. The values of one page are kept in its ``PageStepState``. The values of the odd pages, the even
pages and a group are not the pages' own, since a page added later, or moved to another place, takes them without being
touched, so they are kept once for the step as ``StepValues``.

``StepValueLayers`` is the one place that decides which value a page runs with. A run, a preview and whatever shows the
effective value of a field read it, so they can not disagree.
"""

from typing import TYPE_CHECKING, Any

from attrs import field, frozen

from bookreviver.domain.enums import ValueScope

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, ProjectId, StepId
    from bookreviver.domain.values import MetadataMap

NO_PAGES_NAMED: str = 'A value for pages names at least one page.'
PAGES_UNEXPECTED: str = 'A value for {scope} names no pages.'
GROUP_LABEL_MISSING: str = 'A value for a group names the label of the group.'
GROUP_LABEL_UNEXPECTED: str = 'A value for {scope} names no group label.'
NOT_A_PART: str = 'A value for pages is kept by the pages themselves, and not once for the step.'

# The places of the parts of the book that are told apart, from the weakest to the strongest: a group beats a side
SIDE_SCOPES: frozenset[ValueScope] = frozenset({ValueScope.ODD, ValueScope.EVEN})
SIDE_COUNT: int = 2
ODD_REMAINDER: int = 1


def pages_changing_side(before: Sequence[PageId], after: Sequence[PageId]) -> list[PageId]:
    """Pick the pages that stand on the other side of the book, odd or even, after a change of their places.

    The place of a page is its index in the order of the book, so a page that stands an odd number of places away from
    where it stood has turned over, and a page that is only in one of the two orders, added or deleted, has not.

    :param before: The pages of the book in order before the change.
    :type before: Sequence[PageId]
    :param after: The pages of the book in order after the change.
    :type after: Sequence[PageId]
    :returns: The pages of both orders whose place changed by an odd number, in the order they have after the change.
    :rtype: list[PageId]
    """
    earlier = {page_id: place for place, page_id in enumerate(before)}
    return [
        page_id
        for place, page_id in enumerate(after)
        if page_id in earlier and (place - earlier[page_id]) % SIDE_COUNT == ODD_REMAINDER
    ]


@frozen
class StepValuesKey:
    """The key a part of the pages keeps its values of a step under.

    The project is part of the key, since two books built from one profile keep the identifiers of its steps, and the
    values of one must never reach a run or a save of the other.

    :ivar project_id: Project owning the step.
    :ivar step_id: The step of a recipe.
    :ivar scope: The odd pages, the even pages or a group.
    :ivar group_label: Label of the group for the scope of a group, and empty for the others.
    """

    project_id: ProjectId
    step_id: StepId
    scope: ValueScope
    group_label: str = ''


@frozen(kw_only=True)
class ValueTarget:
    """The pages a value of a setting is set for or taken back from.

    :ivar scope: The kind of part of the pages.
    :ivar page_ids: The pages, for the scope of pages: the open page, or the pages the user selected.
    :ivar group_label: Label of the group, for the scope of a group.
    """

    scope: ValueScope
    page_ids: tuple[PageId, ...] = ()
    group_label: str = ''

    def __attrs_post_init__(self) -> None:
        """Check that the target names what its scope needs and nothing else.

        :raises ValueError: If pages are named for another scope than pages, or none for it, or a label is named for
                            another scope than a group, or none for it.
        """
        if self.scope is ValueScope.PAGES and not self.page_ids:
            raise ValueError(NO_PAGES_NAMED)
        if self.scope is not ValueScope.PAGES and self.page_ids:
            raise ValueError(PAGES_UNEXPECTED.format(scope=self.scope.label.lower()))
        if self.scope is ValueScope.GROUP and not self.group_label:
            raise ValueError(GROUP_LABEL_MISSING)
        if self.scope is not ValueScope.GROUP and self.group_label:
            raise ValueError(GROUP_LABEL_UNEXPECTED.format(scope=self.scope.label.lower()))


@frozen(kw_only=True)
class ValueField:
    """One field of the parameters of one step, and the pages a value of it is set for or taken back from.

    :ivar stage: The stage of the step.
    :ivar step_id: The step of a recipe.
    :ivar name: Name of the field in the parameters of the step.
    :ivar target: The pages, the odd pages, the even pages or the group.
    """

    stage: Stage
    step_id: StepId
    name: str
    target: ValueTarget


@frozen(kw_only=True)
class StepValues:
    """The values of the fields of one step for the odd pages, the even pages, or the pages of one group.

    :ivar project_id: Project owning the step, whose deletion removes the values.
    :ivar stage: Stage of the step.
    :ivar step_id: The step of a recipe.
    :ivar scope: The odd pages, the even pages or a group.
    :ivar group_label: Label of the group for the scope of a group, and empty for the others.
    :ivar params: The fields of the parameters of the step that the part of the pages changes, by name.
    :ivar updated_at: When the values were last saved.
    """

    project_id: ProjectId
    stage: Stage
    step_id: StepId
    scope: ValueScope
    group_label: str = ''
    params: MetadataMap = field(factory=dict)
    updated_at: datetime

    def __attrs_post_init__(self) -> None:
        """Check that the scope is a part of the pages and has the label it needs.

        :raises ValueError: If the scope is pages, or a group has no label, or another part has one.
        """
        if not self.scope.is_part:
            raise ValueError(NOT_A_PART)
        if (self.scope is ValueScope.GROUP) != bool(self.group_label):
            raise ValueError(GROUP_LABEL_MISSING if self.scope is ValueScope.GROUP else GROUP_LABEL_UNEXPECTED)

    @property
    def key(self) -> StepValuesKey:
        """The key the values are stored under."""
        return StepValuesKey(self.project_id, self.step_id, self.scope, self.group_label)

    @property
    def is_empty(self) -> bool:
        """Whether no field is changed, so there is nothing left to store."""
        return not self.params

    def covers(self, *, group_label: str, position: int) -> bool:
        """Tell whether a page is one of the pages the values are for.

        :param group_label: Label of the group the page is in, or empty for a page in none.
        :type group_label: str
        :param position: Place of the page in the book counted from 1, whose parity tells the odd pages from the even.
        :type position: int
        :returns: Whether the page takes these values.
        :rtype: bool
        """
        match self.scope:
            case ValueScope.ODD:
                return position % SIDE_COUNT == ODD_REMAINDER
            case ValueScope.EVEN:
                return position % SIDE_COUNT != ODD_REMAINDER
            case _:
                return bool(group_label) and group_label == self.group_label


@frozen
class StepValueLayers:
    """The values of one step for the parts of the pages, which lie between the recipe and the values of a page.

    :ivar parts: The values of the odd pages, the even pages and the groups, as stored for the step.
    """

    parts: Sequence[StepValues] = ()

    def of_page(self, *, group_label: str, position: int) -> dict[str, Any]:
        """Collect what the parts a page belongs to change, the group over the side.

        :param group_label: Label of the group the page is in, or empty for a page in none.
        :type group_label: str
        :param position: Place of the page in the book counted from 1.
        :type position: int
        :returns: The fields the parts change, with the value the strongest of them gives.
        :rtype: dict[str, Any]
        """
        covering = [part for part in self.parts if part.covers(group_label=group_label, position=position)]
        # A group is stronger than a side, and the sort keeps the order of the parts of one strength
        covering.sort(key=lambda part: part.scope not in SIDE_SCOPES)
        merged: dict[str, Any] = {}
        for part in covering:
            merged.update(part.params)
        return merged

    def lay_over(
        self, params: MetadataMap, *, group_label: str, position: int, own: MetadataMap | None = None
    ) -> dict[str, Any]:
        """Work out the parameters a step runs with on a page: the recipe, then the side, the group and the page.

        :param params: Parameters of the step as the recipe, or the form of a preview, gives them.
        :type params: MetadataMap
        :param group_label: Label of the group the page is in, or empty for a page in none.
        :type group_label: str
        :param position: Place of the page in the book counted from 1.
        :type position: int
        :param own: The fields the page changes for itself, or None for a page that changes none.
        :type own: MetadataMap | None
        :returns: The parameters with each field taken from the strongest part that has a value for it.
        :rtype: dict[str, Any]
        """
        return {**params, **self.of_page(group_label=group_label, position=position), **(own or {})}


@frozen(kw_only=True)
class PageStepSettings:
    """What a page runs one step of a stage with, and where each value comes from.

    :ivar step_id: The step of a recipe.
    :ivar params: The fields the page changes for itself, by name.
    :ivar parts: The values of the odd pages, the even pages and the groups for the step, whichever of them the page is
                 in, so every value that can be taken back is listed.
    :ivar effective: The parameters the step runs with on the page: the recipe, then the side, the group and the page.
    :ivar updated_at: When the settings of the page were last saved, or None for a page that changes no field itself.
    """

    step_id: StepId
    params: MetadataMap = field(factory=dict)
    parts: Sequence[StepValues] = ()
    effective: MetadataMap = field(factory=dict)
    updated_at: datetime | None = None
