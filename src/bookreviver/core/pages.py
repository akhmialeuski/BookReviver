"""The pages of a book and the versions of a page, as one transaction of the book sees them.

``BookPages`` are the pages of the book in book order. It answers for many pages in one read where a window of a book
asks, such as which version a stage reads on each page, so walking a book costs no query for each page. ``BookPage`` is
one page with what belongs to it: its versions, and the record and the current version of each of its stages.

What a stage reads on a page is one rule, which a run and the picture of a row both follow. A stage reads the current
version of the nearest earlier stage of the page whose current version has its images ready, and when no earlier stage
has one, the latest base version that is ready. The page split reads the scan itself, so it has no input version. The
rule only reads: a run that finds an earlier stage out of date brings it up to date first and then asks here.
"""

from collections import defaultdict
from typing import TYPE_CHECKING

from attrs import Factory, field, frozen

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.stages import PageStageKey, Stage
from bookreviver.domain.versions import VersionState

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.ids import PageId, PageVersionId
    from bookreviver.domain.pages import Page
    from bookreviver.domain.stages import PageStage
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories


@frozen
class PageVersions:
    """The versions of one page: the results of every step that ran on it.

    :ivar page_id: The page the versions belong to.
    """

    page_id: PageId
    _repos: BookRepositories = field(repr=False, eq=False)

    def all(self) -> Sequence[PageVersion]:
        """Read every version of the page.

        :returns: The versions of the page, in the order the repository lists them.
        :rtype: Sequence[PageVersion]
        """
        return self._repos.page_versions.list_for_page(self.page_id)

    def get(self, version_id: PageVersionId) -> PageVersion:
        """Read one version of the page.

        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the book holds no such version, or the version belongs to another page.
        """
        version = self._repos.page_versions.get(version_id)
        if version.page_id != self.page_id:
            raise NotFoundError(version_id)
        return version

    def chain(self, head_id: PageVersionId) -> list[PageVersion]:
        """Follow a version back through the versions it was made from, to the base version of the page.

        :param head_id: Identifier of the version to start from, such as the current version of a stage.
        :type head_id: PageVersionId
        :returns: The version and every version before it, the latest first.
        :rtype: list[PageVersion]
        :raises NotFoundError: If the page has no such version, or a version of the chain was deleted.
        """
        chain = [self.get(head_id)]
        while (input_id := chain[-1].input_id) is not None:
            chain.append(self.get(input_id))
        return chain


@frozen
class BookPage:
    """One page of a book with what belongs to it, as the transaction sees it.

    :ivar page: The page as it is stored.
    :ivar versions: The versions of the page.
    """

    page: Page
    _repos: BookRepositories = field(repr=False, eq=False)
    versions: PageVersions = field(
        init=False,
        eq=False,
        repr=False,
        default=Factory(lambda self: PageVersions(self.page.id, self._repos), takes_self=True),
    )

    @property
    def id(self) -> PageId:
        """The identifier of the page."""
        return self.page.id

    def stage(self, stage: Stage) -> PageStage | None:
        """Read where the page stands in a stage.

        :param stage: The stage.
        :type stage: Stage
        :returns: The record of the stage of the page, or None for a page the stage has not run on.
        :rtype: PageStage | None
        """
        return self._repos.page_stages.find(PageStageKey(self.page.id, stage))

    def head(self, stage: Stage) -> PageVersion | None:
        """Read the current version of a stage of the page.

        :param stage: The stage.
        :type stage: Stage
        :returns: The current version, or None for a page the stage has no result of.
        :rtype: PageVersion | None
        """
        record = self.stage(stage)
        if record is None or record.head_version_id is None:
            return None
        return self._repos.page_versions.find(record.head_version_id)


@frozen
class BookPages:
    """The pages of a book, in book order, as the transaction sees them."""

    _repos: BookRepositories = field(repr=False, eq=False)

    def all(self) -> list[BookPage]:
        """Read every page of the book.

        :returns: The pages, in book order.
        :rtype: list[BookPage]
        """
        return [BookPage(page, self._repos) for page in self._repos.pages.list_all()]

    def get(self, page_id: PageId) -> BookPage:
        """Read one page of the book.

        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page.
        :rtype: BookPage
        :raises NotFoundError: If the book has no such page.
        """
        return BookPage(self._repos.pages.get(page_id), self._repos)

    def heads(self, stage: Stage, *, leaving_out: Collection[PageId] = ()) -> list[PageVersion]:
        """Read the current version of a stage of every page of the book but some, in two reads.

        :param stage: The stage.
        :type stage: Stage
        :param leaving_out: Pages whose current version is not wanted.
        :type leaving_out: Collection[PageId]
        :returns: The current versions, one for each page the stage has a result of.
        :rtype: list[PageVersion]
        """
        left_out = set(leaving_out)
        head_ids = {
            record.head_version_id
            for record in self._repos.page_stages.list_for_stage(stage)
            if record.page_id not in left_out and record.head_version_id is not None
        }
        return list(self._repos.page_versions.list_by_ids(head_ids)) if head_ids else []

    def made_by(self, stage: Stage, processor_key: str, *, leaving_out: Collection[PageId] = ()) -> list[PageVersion]:
        """Find the version one processor made on the way to the current version of a stage, on every page but some.

        The current version of a stage is the result of the last step of its recipe, so the version of an earlier step
        is found by following the current one back through the versions of the stage, a level at a time for all the
        pages together.

        :param stage: The stage.
        :type stage: Stage
        :param processor_key: Key of the processor whose versions are wanted.
        :type processor_key: str
        :param leaving_out: Pages that are not read.
        :type leaving_out: Collection[PageId]
        :returns: The versions the processor made, one for each page whose current version came through its step.
        :rtype: list[PageVersion]
        """
        level: Sequence[PageVersion] = self.heads(stage, leaving_out=leaving_out)
        found: list[PageVersion] = []
        while level:
            following: set[PageVersionId] = set()
            for version in level:
                if version.processor.key == processor_key:
                    found.append(version)
                elif version.input_id is not None and version.stage is stage:
                    following.add(version.input_id)
            level = self._repos.page_versions.list_by_ids(following) if following else ()
        return found

    def inputs(self, stage: Stage, page_ids: Collection[PageId]) -> dict[PageId, PageVersion]:
        """Find the version a stage reads on each of some pages.

        :param stage: The stage that reads.
        :type stage: Stage
        :param page_ids: The pages, a window of a book or one page.
        :type page_ids: Collection[PageId]
        :returns: The version read, by page. A page that has none, such as a placeholder, has no entry, and the page
                  split has none for any page, since it reads the scan.
        :rtype: dict[PageId, PageVersion]
        """
        if stage is Stage.PAGE_SPLIT or not page_ids:
            return {}
        found = self._earlier_heads(stage, page_ids)
        if missing := {page_id for page_id in page_ids if page_id not in found}:
            found.update(self.bases(missing))
        return found

    def bases(self, page_ids: Collection[PageId]) -> dict[PageId, PageVersion]:
        """Find the latest base version that is ready of each of some pages, in one read.

        :param page_ids: The pages.
        :type page_ids: Collection[PageId]
        :returns: The base version, by page, with no entry for a page that has none ready.
        :rtype: dict[PageId, PageVersion]
        """
        # The base versions of a page come earliest first, so the last one that is ready is the latest
        return {
            base.page_id: base
            for base in self._repos.page_versions.list_base_versions(page_ids)
            if base.state is VersionState.READY and base.has_image
        }

    def _earlier_heads(self, stage: Stage, page_ids: Collection[PageId]) -> dict[PageId, PageVersion]:
        """Find, for each page, the current version of the nearest earlier stage that has its images ready.

        :param stage: The stage that reads.
        :type stage: Stage
        :param page_ids: The pages.
        :type page_ids: Collection[PageId]
        :returns: The version found, by page, with no entry for a page whose earlier stages have none.
        :rtype: dict[PageId, PageVersion]
        """
        heads: defaultdict[PageId, dict[Stage, PageVersionId]] = defaultdict(dict)
        for record in self._repos.page_stages.list_for_pages(page_ids):
            if record.stage in stage.earlier and record.head_version_id is not None:
                heads[record.page_id][record.stage] = record.head_version_id
        versions = {
            version.id: version
            for version in self._repos.page_versions.list_by_ids(
                {head_id for by_stage in heads.values() for head_id in by_stage.values()}
            )
        }
        found: dict[PageId, PageVersion] = {}
        for page_id, head_ids in heads.items():
            for earlier in stage.earlier:
                head = versions.get(head_ids[earlier]) if earlier in head_ids else None
                if head is not None and head.has_image:
                    found[page_id] = head
                    break
        return found
