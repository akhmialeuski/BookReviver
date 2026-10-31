"""The sides of a page, of a sheet of paper and of an open book."""

from bookreviver.domain.labels import LabeledStrEnum


class Side(LabeledStrEnum):
    """Which side of a page in the book a neighbour or a new position lies on."""

    BEFORE = 'before', 'Before the page'
    AFTER = 'after', 'After the page'


class SheetEdge(LabeledStrEnum):
    """A side of a sheet of paper, named as the reader sees the upright page."""

    TOP = 'top', 'Top'
    RIGHT = 'right', 'Right'
    BOTTOM = 'bottom', 'Bottom'
    LEFT = 'left', 'Left'


class PageSide(LabeledStrEnum):
    """Which side of an open book a page lies on, which its place in the book decides."""

    LEFT = 'left', 'Left page'
    RIGHT = 'right', 'Right page'

    @classmethod
    def of_position(cls, position: int) -> PageSide:
        """Give the side of the page at a place in the book, an odd place being a right page, as in the viewer.

        :param position: Place of the page in the book counted from 1.
        :type position: int
        :returns: The side.
        :rtype: PageSide
        """
        return cls.RIGHT if position % 2 == 1 else cls.LEFT
