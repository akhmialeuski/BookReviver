"""The stage records whose result was made by a processor that has since been replaced by a newer version.

A page version stores the processor that made it, by key and version, and the plugin that provides the processor may be
installed again in a newer version, because the algorithm changed what it makes. The records of the stages built from
the old results stay fresh, since nothing in the data of a page changed, so the workspace would show the old result
and a run would never compute it again. This class finds those records and marks them stale, which is the mark the
interface already shows with the old result and a run already recomputes.

A record is outdated when any version in the chain of its current version was made by a processor that the catalogue
has with another version. The chain is walked back through the versions each one read, across the stages, so a stage
built on an outdated version of an earlier stage is caught as well. A processor the catalogue does not have is never
taken as outdated, since a plugin that is not installed says nothing about its version, and a record that is stale or
failed already is left as it is.

The records of every book are checked, once, when the application starts. No event is published, because nothing is
listening yet: a browser reads the stages of a book when it opens the book, after the application has started.
"""

import logging
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import StageState
from bookreviver.domain.values import PageStageKey

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageStage
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock

logger = logging.getLogger(__name__)


class OutdatedResults:
    """Marks the fresh stage records of all books stale when a processor that made their result has a new version."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, clock: Clock) -> None:
        """Work over the ports of one unit of work.

        :param uow: Unit of work whose commit ends the use case.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run, with the versions that are installed.
        :type catalogue: ProcessorCatalog
        :param clock: Clock stamping the records that change.
        :type clock: Clock
        """
        self._uow = uow
        self._catalogue = catalogue
        self._clock = clock

    async def mark_stale(self) -> list[PageStage]:
        """Mark every fresh record whose result a replaced version of a processor made stale, and commit.

        The chains of all the records are walked together, a level at a time with one read of the versions of the level,
        so the number of queries follows the depth of the chains and not the number of pages.

        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        installed = {spec.key: spec.version for spec in self._catalogue.specs()}
        records = {
            PageStageKey(record.page_id, record.stage): record for record in await self._uow.page_stages.list_fresh()
        }
        walking: dict[PageStageKey, PageVersionId] = {
            key: record.head_version_id for key, record in records.items() if record.head_version_id is not None
        }
        outdated: set[PageStageKey] = set()
        while walking:
            versions = {
                version.id: version for version in await self._uow.page_versions.list_by_ids(set(walking.values()))
            }
            ahead: dict[PageStageKey, PageVersionId] = {}
            for key, version_id in walking.items():
                if (version := versions.get(version_id)) is None:
                    continue
                if installed.get(version.processor.key, version.processor.version) != version.processor.version:
                    outdated.add(key)
                elif version.input_id is not None:
                    ahead[key] = version.input_id
            walking = ahead
        moment = self._clock.now()
        stale: list[PageStage] = []
        for key, record in records.items():
            if key in outdated:
                marked = evolve(record, state=StageState.STALE, updated_at=moment)
                stale.append(await self._uow.page_stages.save(marked))
        await self._uow.commit()
        logger.info('Marked %d stage records stale because a processor of their result has a new version.', len(stale))
        return stale
