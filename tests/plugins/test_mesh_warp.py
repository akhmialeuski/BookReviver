"""Tests for the field that straightens a bent page, and for the mesh file that records it.

The tests need OpenCV, and the module is skipped with the reason where the optional group ``cv`` is not installed.
"""

import json
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ColorMode
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Mesh, Point
from tests.helpers.samples import CV_MISSING

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.plugins.mesh_warp import FlatteningField

mesh_warp = pytest.importorskip('bookreviver.plugins.mesh_warp', reason=CV_MISSING)

WIDTH_PX: int = 600
HEIGHT_PX: int = 400
# The nodes of a curve across the page
NODES: int = 5
# How far the middle of a curve is below its ends, in pixels, and how far from the truth a field may be
BOW_PX: float = 12.0
TOLERANCE_PX: float = 0.05
# The rows of the page the two curves lie on in the flat page
TOP_PX: float = 100.0
BOTTOM_PX: float = 300.0
GRID_ROWS: int = 45
GRID_COLUMNS: int = 31
# The seed of the grain the remapping is tested on, and the stripes of a page drawn black and white
TONE_SEED: int = 8
STRIPE_PX: int = 20
# How many copies of a page make a page taller than a strip, and how many of the last rows are not compared
COPIES: int = 4
EDGE_ROWS_PX: int = 20
# How far the bend measured may be from its truth, in pixels for each thousand of the width, and the truth: the best
# straight line through a parabola lies two thirds of its height above its ends, so the ends depart by that much
BEND_TOLERANCE: float = 0.5
PARABOLA_BEND: float = BOW_PX * 2 / 3 / WIDTH_PX * 1_000
# The lean of two straight curves, as pixels of height for each pixel of width
LEAN: float = 0.02
MESH_NAME: str = 'mesh.json'
ENCODING: str = 'utf-8'


def shift_at(x: float) -> float:
    """Give how far a bowed curve is below its ends at a place of the page.

    :param x: Distance from the left edge in pixels.
    :type x: float
    :returns: The shift in pixels, a parabola that is nothing at the edges and the bow at the middle.
    :rtype: float
    """
    return BOW_PX * (1 - (2 * x / (WIDTH_PX - 1) - 1) ** 2)


def bowed(top: float, bottom: float) -> Mesh:
    """Make two curves that bow downwards in the middle, as the lines of a page bend into the gutter.

    :param top: The height of the top curve at its ends.
    :type top: float
    :param bottom: The height of the bottom curve at its ends.
    :type bottom: float
    :returns: The mesh of the two curves.
    :rtype: Mesh
    """
    xs = np.linspace(0, WIDTH_PX - 1, NODES)
    return Mesh(rows=tuple(tuple(Point(x=float(x), y=float(row + shift_at(x))) for x in xs) for row in (top, bottom)))


class TestFlatteningFieldFromMesh:
    """Tests for the field made of the curves of a mesh."""

    def test_every_row_of_the_flat_page_is_taken_from_the_height_the_bend_gives_it(self) -> None:
        """Verify the grid of the field has each node taken from the row of the flat page moved by the shift of its column.

        The curves have one shape, so the page between them and beyond them moves as the curves do, which is the height
        of the row plus how far the curve is below its ends at the column, less the bow it has at the middle.
        """
        field = mesh_warp.FlatteningField.from_mesh(bowed(TOP_PX, BOTTOM_PX), WIDTH_PX)
        grid = field.grid(HEIGHT_PX)
        rows = np.linspace(0, HEIGHT_PX - 1, GRID_ROWS)
        columns = np.round(np.linspace(0, WIDTH_PX - 1, GRID_COLUMNS))
        truth = rows[:, None] + np.array([shift_at(x) - BOW_PX for x in columns])[None, :]
        expect(bool(np.allclose(grid[:, :, 1], truth, atol=TOLERANCE_PX * 10)))
        expect(bool(np.allclose(grid[:, :, 0], columns[None, :], atol=TOLERANCE_PX)))
        assert_expectations()

    def test_the_bend_is_how_far_a_curve_departs_from_its_straight_line(self) -> None:
        """Verify the bend of curves that bow by 12 pixels over 600 is the departure of a parabola from its best line."""
        bend = mesh_warp.FlatteningField.from_mesh(bowed(TOP_PX, BOTTOM_PX), WIDTH_PX).bend()
        assert abs(bend - PARABOLA_BEND) < BEND_TOLERANCE

    def test_curves_that_are_only_slanted_are_not_bent(self) -> None:
        """Verify two straight curves that lean have no bend, since a lean is a turn and not a bend."""
        xs = np.linspace(0, WIDTH_PX - 1, NODES)
        leaning = Mesh(
            rows=tuple(tuple(Point(x=float(x), y=float(row + x * LEAN)) for x in xs) for row in (TOP_PX, BOTTOM_PX))
        )
        assert mesh_warp.FlatteningField.from_mesh(leaning, WIDTH_PX).bend() < BEND_TOLERANCE

    def test_curves_that_lie_on_one_line_cannot_be_straightened_between(self) -> None:
        """Reject two curves that are at the same height at the middle of the page, which have nothing between them."""
        with pytest.raises(ConflictError, match='one line'):
            mesh_warp.FlatteningField.from_mesh(bowed(TOP_PX, TOP_PX), WIDTH_PX)

    def test_the_curves_may_be_given_in_any_order(self) -> None:
        """Verify a bottom curve given first makes the same field as the top one first."""
        mesh = bowed(TOP_PX, BOTTOM_PX)
        first = mesh_warp.FlatteningField.from_mesh(mesh, WIDTH_PX).grid(HEIGHT_PX)
        swapped = mesh_warp.FlatteningField.from_mesh(Mesh(rows=mesh.rows[::-1]), WIDTH_PX).grid(HEIGHT_PX)
        assert bool(np.allclose(first, swapped))


class TestFlatteningFieldFromGrid:
    """Tests for the field made of the grid of a network."""

    @staticmethod
    def identity() -> np.ndarray:
        """Make the grid that takes every node of the flat page from the same place of the bent one.

        :returns: The grid of the places as x and y in pixels.
        :rtype: np.ndarray
        """
        xs, ys = np.meshgrid(np.linspace(0, WIDTH_PX - 1, GRID_COLUMNS), np.linspace(0, HEIGHT_PX - 1, GRID_ROWS))
        return np.stack([xs, ys], axis=-1)

    def test_a_grid_that_takes_every_pixel_from_itself_changes_nothing(self) -> None:
        """Verify the identity grid has no bend and gives the page back as it was."""
        field = mesh_warp.FlatteningField.from_grid(self.identity(), WIDTH_PX, HEIGHT_PX)
        page = np.random.default_rng(TONE_SEED).integers(0, 255, (HEIGHT_PX, WIDTH_PX), dtype=np.uint8)
        flat = field.remap(page, ColorMode.GRAY)
        expect(field.bend() < TOLERANCE_PX)
        expect(bool(np.all(np.abs(flat.astype(int) - page.astype(int)) <= 1)))
        assert_expectations()

    def test_a_grid_that_moves_the_middle_down_gives_a_bend(self) -> None:
        """Verify a grid whose middle columns are taken from lower in the page has the bend of that move."""
        grid = self.identity()
        grid[:, :, 1] += np.array([shift_at(x) for x in grid[0, :, 0]])[None, :]
        field = mesh_warp.FlatteningField.from_grid(grid, WIDTH_PX, HEIGHT_PX)
        assert abs(field.bend() - PARABOLA_BEND) < BEND_TOLERANCE


class TestFlatteningFieldRemap:
    """Tests for the remapping of an image by a field."""

    @staticmethod
    def field() -> FlatteningField:
        """Make the field of two bowed curves.

        :returns: The field.
        :rtype: FlatteningField
        """
        return cast('FlatteningField', mesh_warp.FlatteningField.from_mesh(bowed(TOP_PX, BOTTOM_PX), WIDTH_PX))

    def test_a_bilevel_page_stays_black_and_white(self) -> None:
        """Verify the grays the resampling makes at the edges of the ink are made black or white again."""
        columns = np.where(np.arange(WIDTH_PX) // STRIPE_PX % 2 == 0, 0, 255).astype(np.uint8)
        page = np.tile(columns, (HEIGHT_PX, 1))
        flat = self.field().remap(page, ColorMode.BILEVEL)
        expect({int(value) for value in np.unique(flat)} <= {0, 255})
        expect(flat.shape == page.shape)
        assert_expectations()

    def test_a_colour_page_keeps_its_planes(self) -> None:
        """Verify the three planes of a colour page are remapped together, and keep their number and their size."""
        page = np.random.default_rng(TONE_SEED).integers(0, 255, (HEIGHT_PX, WIDTH_PX, 3), dtype=np.uint8)
        assert self.field().remap(page, ColorMode.COLOR).shape == page.shape

    def test_a_page_taller_than_a_strip_is_remapped_whole(self) -> None:
        """Verify the strips the page is remapped in join without a seam, so a tall page keeps the rows of a short one."""
        page = np.random.default_rng(TONE_SEED).integers(0, 255, (HEIGHT_PX, WIDTH_PX), dtype=np.uint8)
        tall = np.vstack([page] * COPIES)
        field = self.field()
        flat_tall = field.remap(tall, ColorMode.GRAY)
        # The rows of the first copy that the bend does not carry into another copy are the rows the short page gives
        expect(flat_tall.shape == tall.shape)
        expect(
            bool(
                np.all(
                    flat_tall[: HEIGHT_PX - EDGE_ROWS_PX]
                    == field.remap(page, ColorMode.GRAY)[: HEIGHT_PX - EDGE_ROWS_PX]
                )
            )
        )
        assert_expectations()


class TestMeshFile:
    """Tests for the mesh file of a field."""

    def test_the_file_holds_the_grid_of_the_places_each_node_is_taken_from(self, tmp_path: Path) -> None:
        """Verify the file has the size of the page and a grid of 45 rows and 31 columns of places on the bent page.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        target = tmp_path / MESH_NAME
        mesh_warp.FlatteningField.from_mesh(bowed(TOP_PX, BOTTOM_PX), WIDTH_PX).write(HEIGHT_PX, target)
        document = json.loads(target.read_text(encoding=ENCODING))
        grid = np.array(document['grid'])
        expect((document['width'], document['height']) == (WIDTH_PX, HEIGHT_PX))
        expect(grid.shape == (GRID_ROWS, GRID_COLUMNS, 2))
        # The left edge of the flat page is taken from the left edge of the bent one, and the right from the right
        expect(bool(np.allclose(grid[:, 0, 0], 0, atol=TOLERANCE_PX)))
        expect(bool(np.allclose(grid[:, -1, 0], WIDTH_PX - 1, atol=TOLERANCE_PX)))
        assert_expectations()

    def test_a_file_that_cannot_be_written_is_a_conflict(self, tmp_path: Path) -> None:
        """Verify a path in a directory that does not exist fails with a message, not with an OS error.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        field = mesh_warp.FlatteningField.from_mesh(bowed(TOP_PX, BOTTOM_PX), WIDTH_PX)
        with pytest.raises(ConflictError, match='cannot be written'):
            field.write(HEIGHT_PX, tmp_path / 'missing' / MESH_NAME)
