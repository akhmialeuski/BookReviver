"""What a stage reads on a page: the one rule a run and the picture of a row both follow.

A stage reads the current version of the nearest earlier stage of the page whose current version has its images ready,
and when no earlier stage has one, the latest base version that is ready. The page split reads the scan itself, so it
has no input version. The rule only reads: a run that finds an earlier stage out of date brings it up to date first and
then asks here, and the rows of a stage ask here for the pages of a window, so the workspace draws a page from the very
version a run of the stage would read.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from bookreviver.domain.enums import Stage, VersionState

if TYPE_CHECKING:
    from collections.abc import Collection

    from bookreviver.domain.entities import PageVersion
    from bookreviver.domain.ids import PageId, PageVersionId
    from bookreviver.ports.persistence import UnitOfWork


class StageInputs:
    """Finds the version a stage reads on pages, with the same number of queries for any number of pages."""

    def __init__(self, *, uow: UnitOfWork) -> None:
        """Read over the ports of one request or job.

        :param uow: Unit of work, which is only read.
        :type uow: UnitOfWork
        """
        self._uow = uow

    @staticmethod
    def has_image(version: PageVersion) -> bool:
        """Tell whether a version has its images published, which is what makes it something a stage can read.

        :param version: The version.
        :type version: PageVersion
        :returns: True when the version has an image and its files are ready.
        :rtype: bool
        """
        return version.renditions is not None and version.renditions.ready

    async def of(self, page_ids: Collection[PageId], stage: Stage) -> dict[PageId, PageVersion]:
        """Find the version a stage reads on each of the pages.

        :param page_ids: The pages, a window of a book or one page.
        :type page_ids: Collection[PageId]
        :param stage: The stage that reads.
        :type stage: Stage
        :returns: The version read, by page. A page that has none, such as a placeholder, has no entry, and the page
                  split has none for any page, since it reads the scan.
        :rtype: dict[PageId, PageVersion]
        """
        if stage is Stage.PAGE_SPLIT or not page_ids:
            return {}
        heads: defaultdict[PageId, dict[Stage, PageVersionId]] = defaultdict(dict)
        for record in await self._uow.page_stages.list_for_pages(page_ids):
            if record.stage in stage.earlier and record.head_version_id is not None:
                heads[record.page_id][record.stage] = record.head_version_id
        versions = {
            version.id: version
            for version in await self._uow.page_versions.list_by_ids(
                {head_id for by_stage in heads.values() for head_id in by_stage.values()}
            )
        }
        found: dict[PageId, PageVersion] = {}
        for page_id, head_ids in heads.items():
            for earlier in stage.earlier:
                head = versions.get(head_ids[earlier]) if earlier in head_ids else None
                if head is not None and self.has_image(head):
                    found[page_id] = head
                    break
        if missing := {page_id for page_id in page_ids if page_id not in found}:
            # The base versions of a page come earliest first, so the last one that is ready is the latest
            for base in await self._uow.page_versions.list_base_versions(missing):
                if base.state is VersionState.READY and self.has_image(base):
                    found[base.page_id] = base
        return found
