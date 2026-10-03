import { clampToScan } from '@/features/editors/line';
import type { MeshShape, Point, Size } from '@/features/editors/shapes';
import type { PageResult } from '@/features/processing/results';

/**
 * The arithmetic of the curves editor: the two curves a page starts from, the full grid, and how a node moves.
 *
 * Everything is in the pixels of the image the dewarping step reads. The simple form is the top curve and the bottom
 * curve, each of five nodes, which the reader lays on the first and the last line of text; the server builds the page
 * mesh from them. The full grid has rows between, which "More control" shows.
 */

/** The nodes of a curve, which is what the server's own summary of the curves has. */
export const CURVE_NODES = 5;
/** The rows of the full grid, the top curve, the bottom curve and three between. */
export const GRID_ROWS = 5;
/** The rows of the simple form. */
export const CURVES = 2;

/** Where the two curves start on a page with none found, as shares of the height of the image. */
const TOP_CURVE_SHARE = 0.1;
const BOTTOM_CURVE_SHARE = 0.9;

/** Give the nodes of a straight curve across the image at a height. */
function straightRow(size: Size, y: number): Point[] {
  return Array.from({ length: CURVE_NODES }, (_unused, column) => ({
    x: (size.width * column) / (CURVE_NODES - 1),
    y,
  }));
}

/** Give the two straight curves a page starts from when the step found no lines on it. */
export function straightMesh(size: Size): MeshShape {
  return {
    rows: [
      straightRow(size, size.height * TOP_CURVE_SHARE),
      straightRow(size, size.height * BOTTOM_CURVE_SHARE),
    ],
  };
}

/** Give the mesh to start the editor from: the curves the step found, else two straight ones. */
export function meshOf(result: PageResult | null, size: Size | null): MeshShape {
  return result?.mesh ?? straightMesh(size ?? { width: 1, height: 1 });
}

/** Give the top curve and the bottom curve of a mesh, the simple form of it. */
export function curvesOf(mesh: MeshShape): MeshShape {
  const top = mesh.rows[0];
  const bottom = mesh.rows.at(-1);
  return { rows: top === undefined || bottom === undefined ? [] : [top, bottom] };
}

/**
 * Give the mesh with as many rows as the full grid has.
 *
 * A mesh that has fewer rows gets the rows between its top curve and its bottom curve at even steps from one to the
 * other, which is what the server makes of two curves. A mesh that has as many already is given as it is.
 */
export function gridOf(mesh: MeshShape, rows: number = GRID_ROWS): MeshShape {
  const top = mesh.rows[0];
  const bottom = mesh.rows.at(-1);
  if (mesh.rows.length >= rows || top === undefined || bottom === undefined) {
    return mesh;
  }
  return {
    rows: Array.from({ length: rows }, (_unused, row) => {
      const share = row / (rows - 1);
      return top.map((node, column) => {
        const other = bottom[column] ?? node;
        return { x: node.x + (other.x - node.x) * share, y: node.y + (other.y - node.y) * share };
      });
    }),
  };
}

/**
 * Move one node to a point.
 *
 * @param mesh The mesh.
 * @param row The row of the node, from the top.
 * @param column The column of the node, from the left.
 * @param to Where the pointer is, in the pixels of the image.
 * @param size The size of the image.
 * @returns The mesh with the node on the image.
 */
export function moveNode(
  mesh: MeshShape,
  row: number,
  column: number,
  to: Point,
  size: Size,
): MeshShape {
  const moved = clampToScan(to, size);
  return {
    rows: mesh.rows.map((nodes, index) =>
      index === row ? nodes.map((node, place) => (place === column ? moved : node)) : nodes,
    ),
  };
}
