"""DjVu sources: accepted as uploads and registered as a format, while reading them is still to be written.

An uploaded DjVu file is grouped as a DjVu source and reaches this format through ``SourceReader``, so the import
reports a clear refusal instead of treating the file as something else. A source is one bundled document holding
every page, one single-page file, or an indirect document: an index file with one file per page, which together make
one source. Only the index file names its page files, in a directory that DjVuLibre decodes, so until reading DjVu is
written this format keeps the grouping of the base class, every file a source of its own. Reading the pages and
assembling an indirect document are the task "BR - Task - DjVu source import", which overrides ``group`` and fills in
both methods without touching the ports or the other formats.
"""

from typing import TYPE_CHECKING, NoReturn, override

from bookreviver.adapters.imaging.reader import SourceFormat
from bookreviver.domain.enums import SourceKind
from bookreviver.domain.errors import UnsupportedSourceError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

NOT_YET_SUPPORTED: str = 'Importing DjVu files is not supported yet. Convert the book to PDF or page images for now.'


class DjvuFormat(SourceFormat):
    """Refuses every DjVu source until reading DjVu pages is implemented."""

    kind = SourceKind.DJVU

    @override
    def inspect(self, files: Sequence[Path]) -> NoReturn:
        """Refuse the source, since DjVu pages cannot be read yet.

        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :returns: Nothing, since it always raises.
        :rtype: NoReturn
        :raises UnsupportedSourceError: Always.
        """
        raise UnsupportedSourceError(NOT_YET_SUPPORTED)

    @override
    def extract(self, files: Sequence[Path], *, number: int, target: Path) -> NoReturn:
        """Refuse the scan, since DjVu pages cannot be read yet.

        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :param number: Number of the scan in its source, starting at 0.
        :type number: int
        :param target: Path to write the JPEG at.
        :type target: Path
        :returns: Nothing, since it always raises.
        :rtype: NoReturn
        :raises UnsupportedSourceError: Always.
        """
        raise UnsupportedSourceError(NOT_YET_SUPPORTED)
