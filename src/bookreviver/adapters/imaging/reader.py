"""One entry point for reading every kind of source, dispatching to the format that handles the kind.

The ``SourceInspector`` and ``PageRasterizer`` ports take the ``SourceKind`` of the upload, and ``SourceReader``
implements both by handing the call to the ``SourceFormat`` registered for that kind. Each format is a class of its
own, in a module of its own, holding both halves of its work, so the facts it reports and the pages it writes always
agree. Supporting another kind of source is one more ``SourceFormat`` and one more entry in the imaging provider;
the ports, the services and the other formats stay as they are.

The formats are synchronous, because PyMuPDF, Pillow and the other readers block, and ``SourceReader`` runs each call
in a worker thread.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, override

from asyncer import asyncify

from bookreviver.domain.enums import SourceKind
from bookreviver.ports.imaging import PageRasterizer, SourceInspector

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from bookreviver.domain.values import SourceAnalysis


class SourceFormat(ABC):
    """Reads the pages of one kind of source and writes each of them as a JPEG.

    :ivar kind: The kind of source this format reads.
    """

    kind: ClassVar[SourceKind]

    @abstractmethod
    def inspect(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe the source made of ``files``.

        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :returns: Facts of every page in book order, file metadata and suggested description fields.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        """

    @abstractmethod
    def extract(self, files: Sequence[Path], *, index: int, target: Path) -> None:
        """Write page ``index`` of the source as a JPEG at ``target``, without re-encoding when possible.

        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :param index: Position of the page in book order, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        """


class SourceReader(SourceInspector, PageRasterizer):
    """Inspects and rasterises a source through the format registered for its kind."""

    def __init__(self, *, formats: Iterable[SourceFormat]) -> None:
        """Register one format per kind of source.

        :param formats: The formats to dispatch to, exactly one for every ``SourceKind``.
        :type formats: Iterable[SourceFormat]
        :raises ValueError: If a kind has no format or more than one, which is a wiring mistake in the provider.
        """
        registered = tuple(formats)
        self._formats = {source_format.kind: source_format for source_format in registered}
        missing = set(SourceKind) - self._formats.keys()
        if missing or len(self._formats) != len(registered):
            err_msg = f'Register exactly one source format per kind; missing: {sorted(missing)}, given: {registered}.'
            raise ValueError(err_msg)

    @override
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        """Describe the source with the format of its kind, in a worker thread.

        :param kind: The kind of the source, which selects the format.
        :type kind: SourceKind
        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :returns: Facts of every page in book order, file metadata and suggested description fields.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        """
        return await asyncify(self._formats[kind].inspect)(files)

    @override
    async def extract(self, kind: SourceKind, files: Sequence[Path], index: int, target: Path) -> None:
        """Write one page as JPEG with the format of its source's kind, in a worker thread.

        :param kind: The kind of the source, which selects the format.
        :type kind: SourceKind
        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :param index: Position of the page in book order, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        """
        await asyncify(self._formats[kind].extract)(files, index=index, target=target)
