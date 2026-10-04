"""A page label writer that keeps the numbering in memory, for the tests of the use cases that will export a book."""

from typing import TYPE_CHECKING, override

from bookreviver.ports.imaging import PageLabelWriter

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.page_label_rules import BookLabeling


class MemoryPageLabelWriter(PageLabelWriter):
    """Remembers the numbering written for each target, and touches no file.

    :ivar written: The numbering last written for each target path.
    """

    def __init__(self) -> None:
        """Start with nothing written."""
        self.written: dict[Path, BookLabeling] = {}

    @override
    async def write(self, target: Path, labeling: BookLabeling) -> None:
        """Remember the numbering as the one of the target, replacing an earlier one.

        :param target: The path the numbering is for.
        :type target: Path
        :param labeling: The numbering of the book.
        :type labeling: BookLabeling
        """
        self.written[target] = labeling
