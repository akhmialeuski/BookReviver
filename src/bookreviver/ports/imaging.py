"""Imaging ports: reading a source, extracting page images and cutting tile pyramids.

Import runs these ports in order. The ``SourceInspector`` describes the pages of an upload, the ``PageRasterizer``
writes each page as a JPEG at its native resolution, and the ``Tiler`` cuts that JPEG into the tile pyramid and the
thumbnail the viewer shows. All three read and write local paths handed out by the storage ports.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.enums import SourceKind
    from bookreviver.domain.values import SourceAnalysis


class SourceInspector(ABC):
    """Reads the pages, technical facts and embedded metadata of a source."""

    @abstractmethod
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        """Describe the source made of ``files``.

        :param kind: Whether the files are one PDF or a set of page images.
        :type kind: SourceKind
        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :returns: Facts of every page in book order, file metadata and suggested description fields.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        """


class PageRasterizer(ABC):
    """Produces the native-resolution image of one page."""

    @abstractmethod
    async def extract(self, kind: SourceKind, files: Sequence[Path], index: int, target: Path) -> None:
        """Write page ``index`` of the source as a JPEG at ``target``, without re-encoding when possible.

        :param kind: Whether the files are one PDF or a set of page images.
        :type kind: SourceKind
        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :param index: Position of the page in book order, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
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
