"""Imaging ports: grouping an upload into sources, reading one source, extracting scans and cutting tile pyramids.

Import runs these ports in order. The ``SourceInspector`` first splits the staged files of an upload into sources,
since which files make one source depends on the format, and then describes each source and its scans on its own,
so one unreadable file never stops the others. The ``PageRasterizer`` writes each scan of a source as a JPEG or a PNG at
its native resolution, in the format the caller chose from the scan's colour and the project's image policy, and the
``Tiler`` cuts that image into the tile pyramid and the thumbnail the viewer shows. All three read and write local
paths handed out by the storage ports.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from bookreviver.domain.enums import Rendition, SourceKind
    from bookreviver.domain.values import SourceAnalysis, UploadedSource


class SourceInspector(ABC):
    """Groups the files of an upload into sources, and reads the scans, facts and metadata of one source."""

    @abstractmethod
    async def group(self, files: Mapping[str, Path]) -> Sequence[UploadedSource]:
        """Split the staged files of an upload into sources, any mix of kinds being accepted.

        Every file is a source of its own, except the index file and page files of an indirect DjVu document, which
        make one source together. The order of the upload is the order of the book, so nothing here sorts the files.

        :param files: Local paths of the staged files by their relative name in the upload, in the order the user gave
                      them. Two files can share a base name in different folders.
        :type files: Mapping[str, Path]
        :returns: The sources in the order of their main files in ``files``, which is the order their pages join the
                  book, whatever the names are. The names of a source are keys of ``files``.
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
    async def extract(
        self, kind: SourceKind, files: Sequence[Path], number: int, target: Path, *, full: Rendition
    ) -> None:
        """Write scan ``number`` of the source at ``target`` in the format ``full``, without re-encoding when possible.

        The caller chooses the format, because it knows the project's image policy, which no source knows. A JPEG
        that is already what the scan shows is copied byte for byte when ``full`` is a JPEG. A bilevel scan written as
        a PNG is a 1-bit PNG with exactly two values, and no format smooths its strokes.

        :param kind: Kind of the source, as ``group`` found it.
        :type kind: SourceKind
        :param files: Local paths of the files of the source, in any order.
        :type files: Sequence[Path]
        :param number: Number of the scan in its source, starting at 0.
        :type number: int
        :param target: Path to write the image at, whose name the caller has chosen to match ``full``.
        :type target: Path
        :param full: Format to write, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
        :type full: Rendition
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises IndexError: If the source has no scan ``number``.
        :raises ValueError: If the number of files does not fit the kind, such as two files for a PDF source, or if
                            ``full`` is not a format of the ``full`` image.
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
    async def preview(self, image: Path, target: Path) -> None:
        """Write a JPEG of ``image`` for interactive previews at ``target``, never larger than the image.

        :param image: Page image to shrink.
        :type image: Path
        :param target: Path to write the preview at.
        :type target: Path
        """

    @abstractmethod
    async def thumbnail(self, image: Path, target: Path) -> None:
        """Write a small JPEG thumbnail of ``image`` at ``target``.

        :param image: Page image to shrink.
        :type image: Path
        :param target: Path to write the thumbnail at.
        :type target: Path
        """
