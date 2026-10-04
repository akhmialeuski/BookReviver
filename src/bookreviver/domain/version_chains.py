"""Which old page versions nothing needs any more, worked out from the inputs the versions read.

A page version reads the version before it, its input, so the versions of a page form chains, and the current version
of a stage, its head, needs every version in its chain to show where its image came from and to be remade from. A
version that is not old enough to go, or is a base version, stays, and so does every version it reads. A version that
stays keeps the versions of its chain whatever their age, because deleting an input would leave the version that reads
it without one: the database sets the input of the survivor to none, and the survivor would look like a base version
that nothing may ever delete.

The rule is the same for every adapter of the persistence ports, so it is written once here, over plain identifiers.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bookreviver.domain.entities import PageVersion
    from bookreviver.domain.ids import PageVersionId


def collectable_versions(
    inputs: Mapping[PageVersionId, PageVersionId | None],
    *,
    eligible: Collection[PageVersionId],
    heads: Collection[PageVersionId],
) -> set[PageVersionId]:
    """Choose the versions that may be deleted: the eligible ones that no version that stays reads, directly or not.

    :param inputs: The input of every version of a project, or None for a base version, which has none.
    :type inputs: Mapping[PageVersionId, PageVersionId | None]
    :param eligible: The versions that are old enough to go and are not base versions.
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
