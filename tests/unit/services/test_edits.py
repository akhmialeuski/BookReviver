"""Tests for the shapes of the manual edits of a page, which need no stored book."""

from bookreviver.domain.geometry import EditorKind, Line, Point
from bookreviver.domain.page_steps import NewPageEdit


def test_line_is_the_shape_of_the_split_editor() -> None:
    """Verify the split line the spread plugin reads is drawn by the line editor."""
    line = Line(start=Point(x=1, y=0), end=Point(x=2, y=10))
    assert NewPageEdit(kind=EditorKind.LINE, geometry=line).geometry == line
