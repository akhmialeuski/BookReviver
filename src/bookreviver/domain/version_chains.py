"""Which page versions nothing needs any more, worked out from the inputs the versions read.

A page version reads the version before it, its input, so the versions of a page form chains, and the current version
of a stage, its head, needs every version in its chain to show where its image came from and to be remade from. A
version that is not eligible to go, or is a base version, stays, and so does every version it reads. A version that
stays keeps the versions of its chain whatever their age, because deleting an input would leave the version that reads
it without one: the database sets the input of the survivor to none, and the survivor would look like a base version
that nothing may ever delete.

The same chains tell which versions belong to one step of a recipe, by the processor that made them and the number of
versions of their stage they read, and which versions go with them when the step is cleared on a page.

The rule is the same for every adapter of the persistence ports, so it is written once here, over plain identifiers.
"""

from collections import defaultdict, deque
from typing import TYPE_CHECKING, Self

from attrs import frozen

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from bookreviver.domain.entities import PageVersion, Recipe
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageVersionId, StepId


def collectable_versions(
    inputs: Mapping[PageVersionId, PageVersionId | None],
    *,
    eligible: Collection[PageVersionId],
    heads: Collection[PageVersionId],
) -> set[PageVersionId]:
    """Choose the versions that may be deleted: the eligible ones that no version that stays reads, directly or not.

    :param inputs: The input of every version of a project, or None for a base version, which has none.
    :type inputs: Mapping[PageVersionId, PageVersionId | None]
    :param eligible: The versions that may go, which are not base versions and, for a preview, are old enough.
    :type eligible: Collection[PageVersionId]
    :param heads: The versions that are the current version of a stage of a page, which stay.
    :type heads: Collection[PageVersionId]
    :returns: The versions that may be deleted together, none of which is read by a version that stays.
    :rtype: set[PageVersionId]
    """
    may_go = set(eligible)
    stays = [version_id for version_id in inputs if version_id not in may_go]
    stays.extend(heads)
    reached: set[PageVersionId] = set()
    while stays:
        if (version_id := stays.pop()) in reached:
            continue
        reached.add(version_id)
        if (input_id := inputs.get(version_id)) is not None:
            stays.append(input_id)
    return may_go - reached


def stage_depths(versions: Sequence[PageVersion]) -> dict[PageVersionId, int]:
    """Count, for each version, how many versions of its own stage it reads, directly or through the chain.

    A run makes one version for each step that is on and each reads the one before, so the depth of a version is the
    place of its step in the chain of the stage. The first step reads a version of an earlier stage, or none, which
    does not count.

    :param versions: The versions of one page, of every stage.
    :type versions: Sequence[PageVersion]
    :returns: The depth of every version, from zero, by its identifier.
    :rtype: dict[PageVersionId, int]
    """
    by_id = {version.id: version for version in versions}
    depths: dict[PageVersionId, int] = {}
    for version in versions:
        depth = 0
        cursor = version
        while cursor.input_id is not None:
            earlier = by_id.get(cursor.input_id)
            if earlier is None or earlier.stage is not version.stage:
                break
            depth += 1
            cursor = earlier
        depths[version.id] = depth
    return depths


def readers_first(versions: Sequence[PageVersion]) -> list[list[PageVersion]]:
    """Group versions by how many of them they read, the most first, so every group is read by none of the later ones.

    A version that reads another of the set stands in a group before it, so deleting the groups in this order never
    deletes an input before the version that reads it, and the versions of one group read none of each other.

    :param versions: The versions to order, in any order.
    :type versions: Sequence[PageVersion]
    :returns: The groups from the versions that read the most of the others down to those that read none, each group in
              the order the versions came in.
    :rtype: list[list[PageVersion]]
    """
    members = {version.id: version for version in versions}
    depths: dict[PageVersionId, int] = {}
    for version in versions:
        unresolved: list[PageVersion] = []
        cursor: PageVersion | None = version
        while cursor is not None and cursor.id not in depths:
            unresolved.append(cursor)
            cursor = None if cursor.input_id is None else members.get(cursor.input_id)
        first = 0 if cursor is None else depths[cursor.id] + 1
        for offset, member in enumerate(reversed(unresolved)):
            depths[member.id] = first + offset
    groups: defaultdict[int, list[PageVersion]] = defaultdict(list)
    for version in versions:
        groups[depths[version.id]].append(version)
    return [groups[depth] for depth in sorted(groups, reverse=True)]


def step_places(recipes: Iterable[Recipe], step_id: StepId) -> frozenset[tuple[str, int]]:
    """Give the places a step has in the recipes of its stage, as the processor and the number of steps before it.

    :param recipes: The recipes of the stage.
    :type recipes: Iterable[Recipe]
    :param step_id: The step.
    :type step_id: StepId
    :returns: The key of the processor of the step and its place among the steps that are on, for each recipe that has
              the step switched on.
    :rtype: frozenset[tuple[str, int]]
    """
    return frozenset(
        (step.processor_key, place)
        for recipe in recipes
        for step in recipe.steps
        if step.step_id == step_id and (place := recipe.place_of(step_id)) is not None
    )


def versions_of_step(
    versions: Sequence[PageVersion],
    stage: Stage,
    places: Collection[tuple[str, int]],
    depths: Mapping[PageVersionId, int],
) -> frozenset[PageVersionId]:
    """Find the versions a step made among the versions of a page, without the versions that read them.

    :param versions: Every version of the page, of every stage.
    :type versions: Sequence[PageVersion]
    :param stage: The stage of the step.
    :type stage: Stage
    :param places: The places of the step in the recipes of the stage, from ``step_places``.
    :type places: Collection[tuple[str, int]]
    :param depths: How many versions of their own stage each version reads, from ``stage_depths``.
    :type depths: Mapping[PageVersionId, int]
    :returns: The versions of the stage whose processor and depth are one of the places.
    :rtype: frozenset[PageVersionId]
    """
    return frozenset(
        version.id
        for version in versions
        if version.stage is stage and (version.processor.key, depths[version.id]) in places
    )


@frozen
class StepVersions:
    """The versions one step of a stage made on a page, and the versions that read them.

    Every step that is on stores one version that reads the one before, so a version belongs to the step whose processor
    made it and whose place is the number of versions of its stage the version reads. A step that was cleared takes its
    versions and every version that read one of them, directly or through a chain, whatever stage that is, since a
    version left without its input would look like a base version of the page.

    :ivar stage: The stage of the step.
    :ivar versions: Every version of the page, of every stage, by identifier.
    :ivar depths: How many versions of their own stage each version reads, which is the place of its step in a chain.
    :ivar made: The versions of the step.
    :ivar doomed: The versions of the step and every version that read one of them.
    """

    stage: Stage
    versions: Mapping[PageVersionId, PageVersion]
    depths: Mapping[PageVersionId, int]
    made: frozenset[PageVersionId]
    doomed: frozenset[PageVersionId]

    @classmethod
    def of(cls, versions: Sequence[PageVersion], stage: Stage, places: Collection[tuple[str, int]]) -> Self:
        """Find the versions of a step among the versions of a page.

        :param versions: Every version of the page, of every stage.
        :type versions: Sequence[PageVersion]
        :param stage: The stage of the step.
        :type stage: Stage
        :param places: The places of the step in the recipes of the stage, from ``step_places``.
        :type places: Collection[tuple[str, int]]
        :returns: The versions of the step and the ones that go with them.
        :rtype: Self
        """
        depths = stage_depths(versions)
        made = versions_of_step(versions, stage, places, depths)
        readers: defaultdict[PageVersionId, list[PageVersionId]] = defaultdict(list)
        for version in versions:
            if version.input_id is not None:
                readers[version.input_id].append(version.id)
        doomed = set(made)
        waiting = deque(made)
        while waiting:
            for reader_id in readers[waiting.popleft()]:
                if reader_id not in doomed:
                    doomed.add(reader_id)
                    waiting.append(reader_id)
        return cls(
            stage=stage,
            versions={version.id: version for version in versions},
            depths=depths,
            made=made,
            doomed=frozenset(doomed),
        )

    def input_of(self, head_id: PageVersionId) -> PageVersionId | None:
        """Give the version a page stands on once the step is cleared, which is the one the step read.

        :param head_id: The current version of the stage, which is one of the doomed versions.
        :type head_id: PageVersionId
        :returns: The version the step of the chain of the head read, or None when that is a version of an earlier
                  stage, which the first step of a recipe reads, or when the chain holds no version of the step.
        :rtype: PageVersionId | None
        """
        cursor = self.versions.get(head_id)
        while cursor is not None and cursor.id not in self.made:
            cursor = None if cursor.input_id is None else self.versions.get(cursor.input_id)
        if cursor is None or cursor.input_id is None:
            return None
        earlier = self.versions.get(cursor.input_id)
        return earlier.id if earlier is not None and earlier.stage is self.stage else None
