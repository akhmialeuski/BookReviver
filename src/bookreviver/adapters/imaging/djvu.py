"""DjVu sources: accepted as uploads and registered as a format, while reading them is still to be written.

An upload of one DjVu file is classified as a DjVu source and reaches this format through ``SourceReader``, so the
import reports a clear refusal instead of treating the file as something else. Reading the pages is the task
"BR - Task - DjVu source import", which fills in both methods without touching the ports or the other formats.
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

        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :returns: Nothing, since it always raises.
        :rtype: NoReturn
        :raises UnsupportedSourceError: Always.
        """
        raise UnsupportedSourceError(NOT_YET_SUPPORTED)

    @override
    def extract(self, files: Sequence[Path], *, index: int, target: Path) -> NoReturn:
        """Refuse the page, since DjVu pages cannot be read yet.

        :param files: Local paths of the source files.
        :type files: Sequence[Path]
        :param index: Position of the page in book order, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        :returns: Nothing, since it always raises.
        :rtype: NoReturn
        :raises UnsupportedSourceError: Always.
        """
        raise UnsupportedSourceError(NOT_YET_SUPPORTED)
