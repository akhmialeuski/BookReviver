"""Tests for the edit shapes and the transform of coordinates."""

import math
from typing import Any

import pytest

from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import EditorKind, TransformKind
from bookreviver.domain.errors import UnsupportedTransformError
from bookreviver.domain.geometry import (
    Line,
    Point,
    Quad,
    Rect,
    Rotation,
    SplitChoice,
    Transform,
    geometry_from_data,
)
from bookreviver.domain.ids import StorageKey

# The left half of a spread 2200 px wide and 1561 px high
QUAD: Quad = Quad(
    top_left=Point(x=0, y=0),
    top_right=Point(x=1100, y=0),
    bottom_right=Point(x=1100, y=1561),
    bottom_left=Point(x=0, y=1561),
)
IDENTITY_MATRIX: tuple[float, ...] = (1, 0, 0, 0, 1, 0, 0, 0, 1)
# The right half of the spread starts 1100 px from the left edge
RIGHT_HALF: tuple[float, ...] = (1, 0, -1100, 0, 1, 0, 0, 0, 1)
# A rotation by 30 degrees counter-clockwise about the origin of a canvas, then a shift of the canvas
ANGLE: float = 30.0


def rotation_matrix(degrees: float, *, shift_x: float = 0.0, shift_y: float = 0.0) -> tuple[float, ...]:
    """Build the matrix of a counter-clockwise rotation in image coordinates, whose y axis points down.

    :param degrees: Angle of the rotation.
    :type degrees: float
    :param shift_x: Shift of the canvas to the right after the rotation.
    :type shift_x: float
    :param shift_y: Shift of the canvas down after the rotation.
    :type shift_y: float
    :returns: Nine numbers of the matrix in rows.
    :rtype: tuple[float, ...]
    """
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (cos, sin, shift_x, -sin, cos, shift_y, 0, 0, 1)


class TestTransform:
    """Tests for the arguments a Transform takes by its kind, and for the mapping of points."""

    @pytest.mark.parametrize(
        'arguments',
        [
            {},
            {'kind': TransformKind.CROP, 'quad': QUAD, 'matrix': RIGHT_HALF},
            {'kind': TransformKind.PERSPECTIVE, 'quad': QUAD, 'matrix': IDENTITY_MATRIX},
            {'kind': TransformKind.ROTATE, 'angle': 0.8, 'matrix': rotation_matrix(0.8)},
            {'kind': TransformKind.MESH, 'mesh_key': StorageKey('projects/book/assets/pages/1/mesh.json')},
        ],
        ids=['identity', 'crop', 'perspective', 'rotate', 'mesh'],
    )
    def test_kind_with_its_arguments_is_accepted(self, arguments: dict[str, Any]) -> None:
        """Verify every kind is built from exactly its own arguments, the identity from none.

        :param arguments: Keyword arguments of the transform.
        :type arguments: dict[str, Any]
        """
        assert Transform(**arguments).kind == arguments.get('kind', TransformKind.IDENTITY)

    @pytest.mark.parametrize(
        'arguments',
        [
            {'kind': TransformKind.CROP},
            {'kind': TransformKind.CROP, 'quad': QUAD},
            {'kind': TransformKind.ROTATE, 'quad': QUAD, 'matrix': IDENTITY_MATRIX},
            {'kind': TransformKind.IDENTITY, 'angle': 1.0},
            {'kind': TransformKind.CROP, 'quad': QUAD, 'matrix': IDENTITY_MATRIX, 'angle': 1.0},
        ],
        ids=['missing-arguments', 'missing-matrix', 'argument-of-another-kind', 'identity-with-argument', 'extra'],
    )
    def test_kind_without_exactly_its_arguments_is_rejected(self, arguments: dict[str, Any]) -> None:
        """Reject a transform missing its arguments or carrying one of another kind, which no step could apply.

        :param arguments: Keyword arguments of the transform.
        :type arguments: dict[str, Any]
        """
        with pytest.raises(ValueError, match='transform takes'):
            Transform(**arguments)

    @pytest.mark.parametrize('matrix', [(1, 0, 0, 0, 1), (0, 0, 0, 0, 0, 0, 0, 0, 1)], ids=['short', 'singular'])
    def test_matrix_without_nine_numbers_or_an_inverse_is_rejected(self, matrix: tuple[float, ...]) -> None:
        """Reject a matrix that cannot map points both ways.

        :param matrix: Numbers of the matrix under test.
        :type matrix: tuple[float, ...]
        """
        with pytest.raises(ValueError, match='nine numbers and an inverse'):
            Transform(kind=TransformKind.CROP, quad=QUAD, matrix=matrix)

    def test_crop_maps_a_point_of_the_output_back_into_the_input(self) -> None:
        """Verify the right half of a spread, which starts at x 1100, maps its own x 0 back to x 1100."""
        crop = Transform(kind=TransformKind.CROP, quad=QUAD, matrix=RIGHT_HALF)
        assert crop.to_input(Point(x=10, y=20)) == Point(x=1110, y=20)

    def test_rotation_maps_a_point_there_and_back_to_the_same_point(self) -> None:
        """Verify to_input undoes to_output, so the chain of transforms leads a point back to its scan."""
        rotate = Transform(
            kind=TransformKind.ROTATE, angle=ANGLE, matrix=rotation_matrix(ANGLE, shift_x=300, shift_y=40)
        )
        back = rotate.to_input(rotate.to_output(Point(x=123.5, y=987.25)))
        assert (round(back.x, 6), round(back.y, 6)) == (123.5, 987.25)

    def test_identity_leaves_a_point_where_it_is(self) -> None:
        """Verify the identity transform needs no matrix."""
        assert Transform().to_input(Point(x=3, y=4)) == Point(x=3, y=4)

    def test_mesh_refuses_to_map_a_point(self) -> None:
        """Reject mapping through a mesh, which no matrix describes."""
        mesh = Transform(kind=TransformKind.MESH, mesh_key=StorageKey('projects/book/assets/pages/1/mesh.json'))
        with pytest.raises(UnsupportedTransformError, match='mesh'):
            mesh.to_input(Point(x=0, y=0))


class TestGeometryFromData:
    """Tests for the data of an edit shape."""

    @pytest.mark.parametrize(
        'shape',
        [
            Rect(left=1, top=2, width=30, height=40),
            QUAD,
            Line(start=Point(x=1100, y=0), end=Point(x=1104.5, y=1561)),
            Rotation(degrees=-0.7),
            SplitChoice(pages=SplitChoice.ONE_PAGE),
            SplitChoice(pages=SplitChoice.TWO_PAGES),
            SplitChoice(pages=SplitChoice.TWO_PAGES, line=Line(start=Point(x=1100, y=0), end=Point(x=1090, y=1561))),
        ],
        ids=['rect', 'quad', 'line', 'rotation', 'one-page', 'two-pages', 'two-pages-with-line'],
    )
    def test_shape_survives_a_round_trip_through_its_data(
        self, shape: Rect | Quad | Line | Rotation | SplitChoice
    ) -> None:
        """Verify the data of a shape rebuilds an equal shape, which is what the database stores.

        :param shape: Shape under test.
        :type shape: Rect | Quad | Line | Rotation | SplitChoice
        """
        assert geometry_from_data(shape.editor, shape.to_data()) == shape


class TestSplitChoice:
    """Tests for the choice of one page or two."""

    def test_the_choice_is_drawn_by_the_split_editor(self) -> None:
        """Verify the choice names the editor that the processor ``split.auto`` offers."""
        assert SplitChoice(pages=SplitChoice.ONE_PAGE).editor is EditorKind.SPLIT

    @pytest.mark.parametrize('pages', [0, 3, -1])
    def test_a_number_of_pages_other_than_one_or_two_is_rejected(self, pages: int) -> None:
        """Reject a scan that is split into no page or into more than two.

        :param pages: The number of pages under test.
        :type pages: int
        """
        with pytest.raises(ValueError, match='must be in'):
            SplitChoice(pages=pages)

    def test_a_scan_kept_whole_has_no_cut_line(self) -> None:
        """Reject a cut line given for a scan that stays one page."""
        with pytest.raises(ValueError, match='no cut line'):
            SplitChoice(pages=SplitChoice.ONE_PAGE, line=Line(start=Point(x=1, y=0), end=Point(x=1, y=9)))

    def test_the_hash_of_the_edit_follows_the_choice(self) -> None:
        """Verify one page and two pages are different edits, so the page versions that read them differ."""
        one, two = (PageEdit.hash_of(SplitChoice(pages=pages), None) for pages in (1, 2))
        assert one != two

    @pytest.mark.parametrize('kind', [EditorKind.NONE, EditorKind.BRUSH_MASK, EditorKind.MESH, EditorKind.REGIONS])
    def test_editor_that_draws_no_shape_is_rejected(self, kind: EditorKind) -> None:
        """Reject an editor that stores a mask or no edit, or whose shape comes with a later step.

        :param kind: Editor without a shape.
        :type kind: EditorKind
        """
        with pytest.raises(ValueError, match='draws no geometry'):
            geometry_from_data(kind, {})
