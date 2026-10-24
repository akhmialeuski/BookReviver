"""What a stage reads on a page: the one rule a run and the picture of a row both follow.

A stage reads the current version of the nearest earlier stage of the page whose current version has its images ready,
and when no earlier stage has one, the latest base version that is ready. The page split reads the scan itself, so it
has no input version. The rule only reads: a run that finds an earlier stage out of date brings it up to date first and
then asks here, and the rows of a stage ask here for the pages of a window, so the workspace draws a page from the very
version a run of the stage would read. The rule is synchronous and takes the repositories of the transaction its caller
runs in, so it is called from the work of ``UnitOfWork.read`` or ``Book.change``.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from bookreviver.domain.enums import Stage, VersionState

if TYPE_CHECKING:
    from collections.abc import Collection

    from bookreviver.domain.entities import PageVersion
    from bookreviver.domain.ids import PageId, PageVersionId
    from bookreviver.ports.persistence import BookRepositories


class StageInputs:
    """Finds the version a stage reads on pages, with the same number of queries for any number of pages.

    The rule keeps no state, so its methods are static and read through the repositories the caller passes.
    """

    @staticmethod
    def has_image(version: PageVersion) -> bool:
        """Tell whether a version has its images published, which is what makes it something a stage can read.

        :param version: The version.
        :type version: PageVersion
        :returns: True when the version has an image and its files are ready.
        :rtype: bool
        """
        return version.renditions is not None and version.renditions.ready

    @staticmethod
    def of(repos: BookRepositories, page_ids: Collection[PageId], stage: Stage) -> dict[PageId, PageVersion]:
        """Find the version a stage reads on each of the pages.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
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
        found = StageInputs._earlier_heads(repos, page_ids, stage)
        if missing := {page_id for page_id in page_ids if page_id not in found}:
            # The base versions of a page come earliest first, so the last one that is ready is the latest
            for base in repos.page_versions.list_base_versions(missing):
                if base.state is VersionState.READY and StageInputs.has_image(base):
                    found[base.page_id] = base
        return found

    @staticmethod
    def _earlier_heads(
        repos: BookRepositories, page_ids: Collection[PageId], stage: Stage
    ) -> dict[PageId, PageVersion]:
        """Find, for each page, the current version of the nearest earlier stage that has its images ready.

        :param repos: Repositories of the transaction the caller runs in, which are only read.
        :type repos: BookRepositories
        :param page_ids: The pages.
        :type page_ids: Collection[PageId]
        :param stage: The stage that reads.
        :type stage: Stage
        :returns: The version found, by page, with no entry for a page whose earlier stages have none.
        :rtype: dict[PageId, PageVersion]
        """
        heads: defaultdict[PageId, dict[Stage, PageVersionId]] = defaultdict(dict)
        for record in repos.page_stages.list_for_pages(page_ids):
            if record.stage in stage.earlier and record.head_version_id is not None:
                heads[record.page_id][record.stage] = record.head_version_id
        versions = {
            version.id: version
            for version in repos.page_versions.list_by_ids(
                {head_id for by_stage in heads.values() for head_id in by_stage.values()}
            )
        }
        found: dict[PageId, PageVersion] = {}
        for page_id, head_ids in heads.items():
            for earlier in stage.earlier:
                head = versions.get(head_ids[earlier]) if earlier in head_ids else None
                if head is not None and StageInputs.has_image(head):
                    found[page_id] = head
                    break
        return found
