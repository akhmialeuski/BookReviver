"""The repository ports of the sources of a book and of the scans each source holds."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.ids import (
    ScanId,
    SourceId,
)
from bookreviver.domain.sources import Scan, Source
from bookreviver.ports.persistence.base import Repository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.slices import Slice, SliceRequest


class SourceRepository(Repository[Source, SourceId]):
    """Sources of the book; the digest of the main file of an imported source is unique.

    A rejected or skipped file is a source row too, and its digest may repeat, since it is no part of the book. Only
    the imported sources count as the book's sources, and the rejected and skipped ones are listed and deleted
    apart, when the reader closes the report of an import.

    Deleting a source removes its scans, and the pages of the book made from them stay without their scan.
    """

    @abstractmethod
    def list_all(self) -> Sequence[Source]:
        """Return the book's imported sources in the order they were imported, ties by identifier.

        :returns: Every imported source of the book, the earliest import first.
        :rtype: Sequence[Source]
        """

    @abstractmethod
    def list_unimported(self) -> Sequence[Source]:
        """Return the book's rejected and skipped files in the order they were recorded, ties by identifier.

        :returns: Every rejected or skipped source of the book, the earliest first.
        :rtype: Sequence[Source]
        """

    @abstractmethod
    def delete_unimported(self) -> None:
        """Delete the book's rejected and skipped sources, which hold no scan, and keep the imported ones."""

    @abstractmethod
    def find_by_sha256(self, sha256: str) -> Source | None:
        """Return the book's imported source whose main file has this digest, which refuses a second upload.

        :param sha256: SHA-256 digest of a main file as lower-case hexadecimal digits.
        :type sha256: str
        :returns: The imported source with this digest, or None when the book has none.
        :rtype: Source | None
        """


class ScanRepository(Repository[Scan, ScanId]):
    """Scans of the sources; the pair of a source and the number of a scan in it is unique."""

    @abstractmethod
    def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source in their order in the source.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source, by number.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    def list_window(self, request: SliceRequest) -> Slice[Scan]:
        """Return the scans of a book, source by source in import order and by number within a source.

        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The scans of the window and the number of all the book's scans.
        :rtype: Slice[Scan]
        """

    @abstractmethod
    def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return several scans in one read, so a window of the book costs one query to learn its sources.

        :param scan_ids: Scans to read; one that is not stored is left out of the result.
        :type scan_ids: Collection[ScanId]
        :returns: The stored scans among them, in no particular order.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    def list_unready(self) -> Sequence[Scan]:
        """Return the scans of a book whose renditions are not ready, which an import cut again.

        :returns: The scans without ready renditions, source by source in import order and by number within a source.
        :rtype: Sequence[Scan]
        """
