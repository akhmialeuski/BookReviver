"""The repository port of the versions of the pages, which the stages and their steps make."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.versions import PageVersion
from bookreviver.ports.persistence.base import Repository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from bookreviver.domain.ids import PageId
    from bookreviver.domain.slices import Slice, SliceRequest
    from bookreviver.domain.stages import Stage
    from bookreviver.domain.versions import PageSize, ResultMark, VersionScale


class PageVersionRepository(Repository[PageVersion, PageVersionId]):
    """Versions of the pages of the book; a version whose input version is deleted keeps its row without the input."""

    @abstractmethod
    def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page in the order they were created, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page, the earliest first.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def list_to_prepare(self, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the versions of a book whose files still have to be written: the pending and the failed ones.

        A failed version is returned with the pending ones, so the next job of the book tries it again and no route
        of its own is needed to repeat it.

        :param processor_keys: Keys of the processors whose versions are returned, those the job knows how to make.
        :type processor_keys: Collection[str]
        :returns: The pending and failed versions made by those processors, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def base_sizes(self) -> Sequence[PageSize]:
        """Return the sizes of the page split's base versions of the included pages that show a scan.

        The median of these is the size of a generated blank leaf. A leaf drawn in place of a scan is a version of the
        page order, so it is not counted. A version that records no size is left out.

        :returns: The recorded size of every such base version, in no particular order.
        :rtype: Sequence[PageSize]
        """

    @abstractmethod
    def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the base versions of several pages in one read, so a window of a book costs one query.

        A base version is a version without an input version, the one a scan or nothing feeds. A page cut from a scan
        again gets a new base version beside the old one, so a page can have several.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, in one read.

        :param version_ids: Identifiers of the versions to read, of which those that are not stored are left out.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier, which a repeated run reuses instead of computing it again.

        :param version_id: Identifier of the version, the hash of what produces it.
        :type version_id: PageVersionId
        :returns: The stored version, ready or failed or still running, or None when no run has made it.
        :rtype: PageVersion | None
        """

    @abstractmethod
    def list_for_stage(
        self,
        page_id: PageId,
        stage: Stage | None,
        scale: VersionScale | None,
        request: SliceRequest,
        mark: ResultMark | None = None,
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page, filtered by stage, scale and mark, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage whose versions are listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale of the runs listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param mark: Mark the versions carry, or None for every version, marked or not.
        :type mark: ResultMark | None
        :returns: The versions of the window and the number of all that match, ties by identifier.
        :rtype: Slice[PageVersion]
        """

    @abstractmethod
    def collectable(self, previews_older_than: datetime) -> Sequence[PageVersion]:
        """Return the versions a collection may delete: not base, and read by no version that stays.

        A version stays when a stage record names it as its head, when it is a base version, which has no input, when
        it is a preview too young to go, when the user marked it Good or wrote a comment on it, and when a version that
        stays reads it, directly or through the chain of inputs. Deleting an input would leave the version that reads
        it with no input, which the database allows and which would make it look like a base version for ever. A full
        run goes as soon as it is none of these, with no age to wait for, and a preview once it was created before
        ``previews_older_than``, since the editor that asked for it is still showing it.

        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """

    @abstractmethod
    def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the rows of several versions in the one transaction; a version that is not stored is left alone.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """
