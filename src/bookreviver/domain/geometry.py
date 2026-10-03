"""Geometry in the pixel coordinates of an image: the shapes of manual edits and the transform of a processing step.

Every coordinate is in pixels of the image the shape or the transform is defined on, whose origin is its top left
corner. The edit shapes are what a user draws, a frame, a quadrilateral, a split line or a rotation, and each knows how
to turn itself into plain JSON data and back, so the persistence adapter and the hash of a manual edit read one format.

A ``Transform`` maps a point of the input image of a step to the output image and back. The matrix is a 3 by 3 matrix of
homogeneous coordinates written by the plugin that knows its algorithm, and the domain only applies it, with the
standard library, so the chain of transforms from a scan to any page version maps coordinates back to the scan without
OpenCV. A mesh is not a matrix, so a dewarping transform names its stored mesh and refuses to map a point until the
step that writes meshes brings the port that reads them.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Self

from attrs import field, frozen, validators

from bookreviver.domain.enums import EditorKind, TransformKind
from bookreviver.domain.errors import UnsupportedTransformError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.domain.ids import StorageKey

# The numbers of a 3 by 3 matrix
MATRIX_SIZE: int = 9
# A determinant this close to zero has no inverse worth using
SINGULAR_LIMIT: float = 1e-12


@frozen(kw_only=True)
class Point:
    """A point in the pixel coordinates of an image, whose origin is its top left corner.

    :ivar x: Distance from the left edge in pixels.
    :ivar y: Distance from the top edge in pixels.
    """

    x: float
    y: float

    def to_data(self) -> dict[str, float]:
        """Return the point as JSON data.

        :returns: The two coordinates by name.
        :rtype: dict[str, float]
        """
        return {'x': self.x, 'y': self.y}


@frozen(kw_only=True)
class Size:
    """The size of an image in pixels.

    :ivar width: Width in pixels.
    :ivar height: Height in pixels.
    """

    width: float = field(validator=validators.gt(0))
    height: float = field(validator=validators.gt(0))


@frozen(kw_only=True)
class Rect:
    """An axis-aligned rectangle, the frame of a crop.

    :ivar left: Distance of the left edge from the left edge of the image.
    :ivar top: Distance of the top edge from the top edge of the image.
    :ivar width: Width in pixels.
    :ivar height: Height in pixels.
    """

    editor: ClassVar[EditorKind] = EditorKind.RECT

    left: float
    top: float
    width: float = field(validator=validators.gt(0))
    height: float = field(validator=validators.gt(0))

    def scaled(self, factor: float) -> Self:
        """Return the rectangle in the pixels of an image resized by a factor.

        :param factor: Size of the new image over the size of the old one.
        :type factor: float
        :returns: The rectangle with every number multiplied by the factor.
        :rtype: Self
        """
        return type(self)(
            left=self.left * factor, top=self.top * factor, width=self.width * factor, height=self.height * factor
        )

    def to_data(self) -> dict[str, float]:
        """Return the rectangle as JSON data.

        :returns: The four numbers by name.
        :rtype: dict[str, float]
        """
        return {'left': self.left, 'top': self.top, 'width': self.width, 'height': self.height}

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Self:
        """Build a rectangle from the JSON data ``to_data`` wrote.

        :param data: The four numbers by name.
        :type data: Mapping[str, Any]
        :returns: The rectangle.
        :rtype: Self
        """
        return cls(left=data['left'], top=data['top'], width=data['width'], height=data['height'])


@frozen(kw_only=True)
class Quad:
    """A quadrilateral, such as a half of a spread or a skewed page.

    :ivar top_left: Corner at the top left of the area.
    :ivar top_right: Corner at the top right of the area.
    :ivar bottom_right: Corner at the bottom right of the area.
    :ivar bottom_left: Corner at the bottom left of the area.
    """

    editor: ClassVar[EditorKind] = EditorKind.QUAD
    CORNERS: ClassVar[tuple[str, ...]] = ('top_left', 'top_right', 'bottom_right', 'bottom_left')

    top_left: Point
    top_right: Point
    bottom_right: Point
    bottom_left: Point

    @classmethod
    def from_points(cls, points: Sequence[Point]) -> Self:
        """Build a quadrilateral from four points in the order of ``CORNERS``.

        :param points: Top left, top right, bottom right and bottom left corner.
        :type points: Sequence[Point]
        :returns: The quadrilateral.
        :rtype: Self
        """
        return cls(**dict(zip(cls.CORNERS, points, strict=True)))

    def points(self) -> tuple[Point, ...]:
        """Return the corners in the order of ``CORNERS``.

        :returns: Top left, top right, bottom right and bottom left corner.
        :rtype: tuple[Point, ...]
        """
        return (self.top_left, self.top_right, self.bottom_right, self.bottom_left)

    def to_data(self) -> dict[str, dict[str, float]]:
        """Return the quadrilateral as JSON data.

        :returns: Every corner by name, as the data of a point.
        :rtype: dict[str, dict[str, float]]
        """
        return {corner: getattr(self, corner).to_data() for corner in self.CORNERS}

    @classmethod
    def from_data(cls, data: Mapping[str, Mapping[str, float]]) -> Self:
        """Build a quadrilateral from the JSON data ``to_data`` wrote.

        :param data: Every corner by name, as the data of a point.
        :type data: Mapping[str, Mapping[str, float]]
        :returns: The quadrilateral.
        :rtype: Self
        """
        return cls(**{corner: Point(**data[corner]) for corner in cls.CORNERS})


@frozen(kw_only=True)
class Line:
    """A straight line through two points, such as the cut that splits a spread into two pages.

    :ivar start: First point of the line, near the top of the image for a cut.
    :ivar end: Second point of the line, near the bottom of the image for a cut.
    """

    editor: ClassVar[EditorKind] = EditorKind.LINE

    start: Point
    end: Point

    def to_data(self) -> dict[str, dict[str, float]]:
        """Return the line as JSON data.

        :returns: Both points by name.
        :rtype: dict[str, dict[str, float]]
        """
        return {'start': self.start.to_data(), 'end': self.end.to_data()}

    @classmethod
    def from_data(cls, data: Mapping[str, Mapping[str, float]]) -> Self:
        """Build a line from the JSON data ``to_data`` wrote.

        :param data: Both points by name.
        :type data: Mapping[str, Mapping[str, float]]
        :returns: The line.
        :rtype: Self
        """
        return cls(start=Point(**data['start']), end=Point(**data['end']))


@frozen(kw_only=True)
class Rotation:
    """A rotation by an angle the user gave instead of the one a plugin finds.

    :ivar degrees: Angle in degrees, counter-clockwise.
    """

    editor: ClassVar[EditorKind] = EditorKind.ROTATION

    degrees: float

    def to_data(self) -> dict[str, float]:
        """Return the rotation as JSON data.

        :returns: The angle by name.
        :rtype: dict[str, float]
        """
        return {'degrees': self.degrees}

    @classmethod
    def from_data(cls, data: Mapping[str, float]) -> Self:
        """Build a rotation from the JSON data ``to_data`` wrote.

        :param data: The angle by name.
        :type data: Mapping[str, float]
        :returns: The rotation.
        :rtype: Self
        """
        return cls(degrees=data['degrees'])


@frozen(kw_only=True)
class SplitChoice:
    """The decision of the user on how a scan is split: into one page or two, and where the cut runs.

    :ivar pages: 1 to keep the scan whole, 2 to cut it into the left page and the right page.
    :ivar line: The cut the user drew, for a scan of two pages, or None to cut where the gutter is found.
    """

    ONE_PAGE: ClassVar[int] = 1
    TWO_PAGES: ClassVar[int] = 2
    PAGES_KEY: ClassVar[str] = 'pages'
    LINE_KEY: ClassVar[str] = 'line'

    editor: ClassVar[EditorKind] = EditorKind.SPLIT

    pages: int = field(validator=validators.in_((ONE_PAGE, TWO_PAGES)))
    line: Line | None = None

    def __attrs_post_init__(self) -> None:
        """Check that a cut is given only for a scan of two pages.

        :raises ValueError: If a scan kept whole has a cut line.
        """
        if self.pages == self.ONE_PAGE and self.line is not None:
            err_msg = 'A scan kept as one page has no cut line.'
            raise ValueError(err_msg)

    def to_data(self) -> dict[str, Any]:
        """Return the choice as JSON data.

        :returns: The number of pages and the line by name, the line being None when none was drawn.
        :rtype: dict[str, Any]
        """
        return {self.PAGES_KEY: self.pages, self.LINE_KEY: None if self.line is None else self.line.to_data()}

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Self:
        """Build a choice from the JSON data ``to_data`` wrote.

        :param data: The number of pages and, if drawn, the line.
        :type data: Mapping[str, Any]
        :returns: The choice.
        :rtype: Self
        """
        line = data.get(cls.LINE_KEY)
        return cls(pages=data[cls.PAGES_KEY], line=None if line is None else Line.from_data(line))


type EditGeometry = Rect | Quad | Line | Rotation | SplitChoice

# The shape each editor draws, which a stored edit is rebuilt by
EDIT_SHAPES: Mapping[EditorKind, type[Rect | Quad | Line | Rotation | SplitChoice]] = {
    EditorKind.RECT: Rect,
    EditorKind.QUAD: Quad,
    EditorKind.LINE: Line,
    EditorKind.ROTATION: Rotation,
    EditorKind.SPLIT: SplitChoice,
}


def geometry_from_data(kind: EditorKind, data: Mapping[str, Any]) -> EditGeometry:
    """Rebuild the shape of an edit from the JSON data its ``to_data`` wrote.

    :param kind: Editor that drew the shape.
    :type kind: EditorKind
    :param data: The data of the shape.
    :type data: Mapping[str, Any]
    :returns: The shape.
    :rtype: EditGeometry
    :raises ValueError: If the editor draws no shape, such as the brush mask, which keeps a mask file instead.
    """
    if (shape := EDIT_SHAPES.get(kind)) is None:
        err_msg = f'The {kind.label.lower()} editor draws no geometry.'
        raise ValueError(err_msg)
    return shape.from_data(data)


@frozen(kw_only=True)
class Transform:
    """The transform of coordinates a processing step applies from its input image to its output image.

    The chain of transforms from a scan to any page version maps coordinates of the version back to the scan. A crop and
    a perspective correction name the quadrilateral of the input they take, a rotation its angle, and a dewarping the
    key of its stored mesh, and the placing of a block of text on a page only the matrix of its scaling and shift. Every
    kind but the identity and the mesh also carries the ``matrix`` that maps a point of the input to the output, nine
    numbers of a 3 by 3 matrix in rows, which the plugin of the step computes.

    :ivar kind: Kind of the transform.
    :ivar quad: Area of the input that becomes the output, for a crop or a perspective correction.
    :ivar angle: Angle of a rotation in degrees, counter-clockwise.
    :ivar mesh_key: Storage key of the mesh a dewarping follows.
    :ivar matrix: Matrix of homogeneous coordinates from the input to the output, row by row.
    """

    ARGUMENTS: ClassVar[Mapping[TransformKind, frozenset[str]]] = {
        TransformKind.IDENTITY: frozenset(),
        TransformKind.CROP: frozenset({'quad', 'matrix'}),
        TransformKind.ROTATE: frozenset({'angle', 'matrix'}),
        TransformKind.PERSPECTIVE: frozenset({'quad', 'matrix'}),
        TransformKind.MESH: frozenset({'mesh_key'}),
        TransformKind.PLACE: frozenset({'matrix'}),
    }

    kind: TransformKind = TransformKind.IDENTITY
    quad: Quad | None = None
    angle: float | None = None
    mesh_key: StorageKey | None = None
    matrix: tuple[float, ...] | None = None

    def __attrs_post_init__(self) -> None:
        """Check that exactly the arguments of the kind are given, and that a matrix has an inverse.

        :raises ValueError: If an argument of the kind is missing or an argument of another kind is given, or the matrix
                            does not have nine numbers or has no inverse.
        """
        every_argument = frozenset[str]().union(*self.ARGUMENTS.values())
        given = {name for name in every_argument if getattr(self, name) is not None}
        if given != (expected := self.ARGUMENTS[self.kind]):
            err_msg = f'A {self.kind} transform takes {sorted(expected) or "no arguments"}, not {sorted(given)}.'
            raise ValueError(err_msg)
        if self.matrix is not None and (
            len(self.matrix) != MATRIX_SIZE or abs(self._determinant(self.matrix)) < SINGULAR_LIMIT
        ):
            err_msg = f'A transform matrix has nine numbers and an inverse, not {self.matrix}.'
            raise ValueError(err_msg)

    def to_output(self, point: Point) -> Point:
        """Return where a point of the input image lies in the output image.

        :param point: Point of the input image.
        :type point: Point
        :returns: The point of the output image.
        :rtype: Point
        :raises UnsupportedTransformError: If the transform is a mesh, which no matrix describes.
        """
        return self._apply(self._matrix(), point)

    def to_input(self, point: Point) -> Point:
        """Return where a point of the output image lies in the input image.

        :param point: Point of the output image.
        :type point: Point
        :returns: The point of the input image.
        :rtype: Point
        :raises UnsupportedTransformError: If the transform is a mesh, which no matrix describes.
        """
        return self._apply(self._inverse(), point)

    def _matrix(self) -> tuple[float, ...]:
        """Return the matrix of the transform, the identity matrix for the identity.

        :returns: Nine numbers of a 3 by 3 matrix in rows.
        :rtype: tuple[float, ...]
        :raises UnsupportedTransformError: If the transform is a mesh.
        """
        if self.kind is TransformKind.MESH:
            err_msg = f'Points cannot be mapped through the mesh {self.mesh_key} yet.'
            raise UnsupportedTransformError(err_msg)
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0) if self.matrix is None else self.matrix

    @staticmethod
    def _determinant(matrix: tuple[float, ...]) -> float:
        """Return the determinant of a matrix.

        :param matrix: Nine numbers of a 3 by 3 matrix in rows.
        :type matrix: tuple[float, ...]
        :returns: The determinant, which is zero for a matrix with no inverse.
        :rtype: float
        """
        a, b, c, d, e, f, g, h, i = matrix
        return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)

    def _inverse(self) -> tuple[float, ...]:
        """Return the inverse of the matrix, by its adjugate.

        :returns: Nine numbers of the inverse in rows.
        :rtype: tuple[float, ...]
        :raises UnsupportedTransformError: If the transform is a mesh.
        """
        matrix = self._matrix()
        a, b, c, d, e, f, g, h, i = matrix
        det = self._determinant(matrix)
        return (
            (e * i - f * h) / det,
            (c * h - b * i) / det,
            (b * f - c * e) / det,
            (f * g - d * i) / det,
            (a * i - c * g) / det,
            (c * d - a * f) / det,
            (d * h - e * g) / det,
            (b * g - a * h) / det,
            (a * e - b * d) / det,
        )

    @staticmethod
    def _apply(matrix: tuple[float, ...], point: Point) -> Point:
        """Apply a matrix of homogeneous coordinates to a point.

        :param matrix: Nine numbers of a 3 by 3 matrix in rows.
        :type matrix: tuple[float, ...]
        :param point: Point to move.
        :type point: Point
        :returns: The moved point, divided by its homogeneous coordinate.
        :rtype: Point
        """
        a, b, c, d, e, f, g, h, i = matrix
        scale = g * point.x + h * point.y + i
        return Point(x=(a * point.x + b * point.y + c) / scale, y=(d * point.x + e * point.y + f) / scale)
