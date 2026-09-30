"""Imaging ports: grouping an upload into sources, reading one source, extracting scans and cutting tile pyramids.

Import runs these ports in order. The ``SourceInspector`` first splits the staged files of an upload into sources,
since which files make one source depends on the format, and then describes each source and its scans on its own,
so one unreadable file never stops the others. The ``PageRasterizer`` writes each scan of a source as a JPEG at its
native resolution, and the ``Tiler`` cuts that JPEG into the tile pyramid and the thumbnail the viewer shows. All
three read and write local paths handed out by the storage ports.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.enums import SourceKind
    from bookreviver.domain.values import SourceAnalysis, UploadedSource


class SourceInspector(ABC):
    """Groups the files of an upload into sources, and reads the scans, facts and metadata of one source."""

    @abstractmethod
    async def group(self, files: Sequence[Path]) -> Sequence[UploadedSource]:
        """Split the staged files of an upload into sources, any mix of kinds being accepted.

        Every file is a source of its own, except the index file and page files of an indirect DjVu document, which
        make one source together.

        :param files: Local paths of the staged files, in any order.
        :type files: Sequence[Path]
        :returns: The sources in the natural order of the names of their main files, so ``part2.pdf`` precedes
                  ``part10.pdf``, which is the order their pages join the book.
        :rtype: Sequence[UploadedSource]
        :raises UploadRejectedError: If there are no files, or the type of a file is not accepted.
        """

    @abstractmethod
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        """Describe one source and its scans.

        :param kind: Kind of the source, as ``group`` found it.
        :type kind: SourceKind
        :param files: Local paths of the files of the source, in any order.
        :type files: Sequence[Path]
        :returns: Facts of every scan in the order of the source, the metadata of the format and suggested
                  description fields.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises ValueError: If the number of files does not fit the kind, such as two files for a PDF source.
        """


class PageRasterizer(ABC):
    """Produces the native-resolution image of one scan of a source."""

    @abstractmethod
    async def extract(self, kind: SourceKind, files: Sequence[Path], number: int, target: Path) -> None:
        """Write scan ``number`` of the source as a JPEG at ``target``, without re-encoding when possible.

        :param kind: Kind of the source, as ``group`` found it.
        :type kind: SourceKind
        :param files: Local paths of the files of the source, in any order.
        :type files: Sequence[Path]
        :param number: Number of the scan in its source, starting at 0.
        :type number: int
        :param target: Path to write the JPEG at.
        :type target: Path
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises IndexError: If the source has no scan ``number``.
        :raises ValueError: If the number of files does not fit the kind, such as two files for a PDF source.
        """


class Tiler(ABC):
    """Cuts a page image into a zoomable tile pyramid and a thumbnail."""

    @abstractmethod
    async def tile(self, image: Path, target_dir: Path, *, resource_id: str) -> None:
        """Write an IIIF Image API level 0 pyramid of ``image`` into ``target_dir``.

        :param image: Page image to cut.
        :type image: Path
        :param target_dir: Directory to create for the pyramid.
        :type target_dir: Path
        :param resource_id: Path the pyramid is served from, such as ``/api/v1/iiif/<key>``, written as the ``id`` of
                            its ``info.json``, since a viewer builds every tile URL from it. It carries no scheme
                            or host, so the browser resolves it against the address the page was opened from.
        :type resource_id: str
        """

    @abstractmethod
    async def thumbnail(self, image: Path, target: Path) -> None:
        """Write a small JPEG preview of ``image`` at ``target``.

        :param image: Page image to shrink.
        :type image: Path
        :param target: Path to write the preview at.
        :type target: Path
        """
