"""One entry point for reading every kind of source, dispatching to the format that handles the kind.

The ``SourceInspector`` and ``PageRasterizer`` ports work on one source at a time, and ``SourceReader`` implements
both by handing each call to the ``SourceFormat`` registered for the ``SourceKind`` of the source. Grouping an upload
into sources is dispatched the same way: the reader finds the kind of every file from its type, and the format of
that kind decides which of its files make one source, since only the format knows, as for an indirect DjVu document.
Each format is a class of its own, in a module of its own, holding every part of its work, so the sources it groups,
the facts it reports and the scans it writes always agree. Supporting another kind of source is one more
``SourceFormat`` and one more entry in the imaging provider; the ports, the services and the other formats stay as
they are.

The formats are synchronous, because PyMuPDF, Pillow and the other readers block, and ``SourceReader`` runs each call
in a worker thread.
"""

from abc import ABC, abstractmethod
from collections import defaultdict
from functools import partial
from typing import TYPE_CHECKING, ClassVar, override

from anyio import to_thread

from bookreviver.domain.enums import FileType, SourceKind, UploadProblem
from bookreviver.domain.errors import UploadRejectedError
from bookreviver.domain.values import UploadedSource
from bookreviver.ports.imaging import PageRasterizer, SourceInspector

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from bookreviver.domain.enums import Rendition
    from bookreviver.domain.values import SourceAnalysis


class SourceFormat(ABC):
    """Groups, describes and rasterises the sources of one kind.

    :ivar kind: The kind of source this format reads.
    """

    kind: ClassVar[SourceKind]

    def group(self, files: Sequence[Path]) -> list[Sequence[Path]]:
        """Split files of this kind into the files of each source; here every file is a source of its own.

        A format whose sources can span several files, as an indirect DjVu document does, overrides this.

        :param files: Local paths of the staged files of this kind, in the order of the upload.
        :type files: Sequence[Path]
        :returns: The files of each source, the main file first.
        :rtype: list[Sequence[Path]]
        """
        return [[path] for path in files]

    @abstractmethod
    def inspect(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe the one source made of ``files``.

        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :returns: Facts of every scan in the order of the source, the metadata of the format and suggested
                  description fields.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises ValueError: If the number of files does not fit the kind.
        """

    @abstractmethod
    def extract(self, files: Sequence[Path], *, number: int, target: Path, full: Rendition) -> None:
        """Write scan ``number`` of the source at ``target`` in the format ``full``, without re-encoding when possible.

        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :param number: Number of the scan in its source, starting at 0.
        :type number: int
        :param target: Path to write the image at.
        :type target: Path
        :param full: Format to write, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
        :type full: Rendition
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises IndexError: If the source has no scan ``number``.
        :raises ValueError: If the number of files does not fit the kind, or ``full`` is not a format of the full image.
        """


class SourceReader(SourceInspector, PageRasterizer):
    """Groups, inspects and rasterises sources through the format registered for their kind."""

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
    async def group(self, files: Mapping[str, Path]) -> Sequence[UploadedSource]:
        """Find the kind of every file from its type, and let the format of each kind group its files.

        The files are never sorted: the order of the upload is the order of the book.

        :param files: Local paths of the staged files by their relative name, in the order of the upload.
        :type files: Mapping[str, Path]
        :returns: The sources in the order of their main files in ``files``.
        :rtype: Sequence[UploadedSource]
        :raises UploadRejectedError: If there are no files, or the type of a file is not accepted.
        """
        if not files:
            raise UploadRejectedError(UploadProblem.NO_FILES)
        ordered = list(files.values())
        names = {path: name for name, path in files.items()}
        file_types: dict[Path, FileType] = {}
        by_kind: defaultdict[SourceKind, list[Path]] = defaultdict(list)
        for path in ordered:
            if (file_type := FileType.from_name(path.name)) is None:
                raise UploadRejectedError(UploadProblem.UNSUPPORTED_TYPE)
            file_types[path] = file_type
            by_kind[file_type.source_kind].append(path)
        groups = [
            (kind, source_files)
            for kind, paths in by_kind.items()
            for source_files in await to_thread.run_sync(self._formats[kind].group, paths)
        ]
        position = {path: index for index, path in enumerate(ordered)}
        groups.sort(key=lambda group: position[group[1][0]])
        return [
            UploadedSource(
                kind=kind, file_type=file_types[source_files[0]], names=[names[path] for path in source_files]
            )
            for kind, source_files in groups
        ]

    @override
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        """Describe one source with the format of its kind, in a worker thread.

        :param kind: The kind of the source, which selects the format.
        :type kind: SourceKind
        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :returns: Facts of every scan in the order of the source, the metadata of the format and suggested
                  description fields.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises ValueError: If the number of files does not fit the kind.
        """
        return await to_thread.run_sync(self._formats[kind].inspect, files)

    @override
    async def extract(
        self, kind: SourceKind, files: Sequence[Path], number: int, target: Path, *, full: Rendition
    ) -> None:
        """Write one scan in the format ``full`` with the format of its source's kind, in a worker thread.

        :param kind: The kind of the source, which selects the format.
        :type kind: SourceKind
        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :param number: Number of the scan in its source, starting at 0.
        :type number: int
        :param target: Path to write the image at.
        :type target: Path
        :param full: Format to write, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
        :type full: Rendition
        :raises UnsupportedSourceError: If the files are not a readable source of this kind.
        :raises IndexError: If the source has no scan ``number``.
        :raises ValueError: If the number of files does not fit the kind, or ``full`` is not a format of the full image.
        """
        await to_thread.run_sync(partial(self._formats[kind].extract, files, number=number, target=target, full=full))
