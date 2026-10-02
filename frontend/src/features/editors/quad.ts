import { clampToScan } from '@/features/editors/line';
import type { Point, QuadShape, Size } from '@/features/editors/shapes';
import type { PageResult } from '@/features/processing/results';

/**
 * The arithmetic of the sheet editor: the four corners of the paper, where they start, and how a corner moves.
 *
 * Everything is in the pixels of the image the step reads, which is what the perspective step of the server reads. A
 * corner never leaves the image, and never crosses to a place that would fold the sheet, since a sheet that is not convex
 * cannot be straightened.
 */

/** Which corner of the sheet. */
export const QuadCorner = {
  TopLeft: 'topLeft',
  TopRight: 'topRight',
  BottomRight: 'bottomRight',
  BottomLeft: 'bottomLeft',
} as const;

/** One corner of the sheet (derived from {@link QuadCorner}). */
export type QuadCorner = (typeof QuadCorner)[keyof typeof QuadCorner];

/** The corners in the order round the sheet from the top left, which is the order the server writes them. */
export const CORNER_ORDER: readonly QuadCorner[] = [
  QuadCorner.TopLeft,
  QuadCorner.TopRight,
  QuadCorner.BottomRight,
  QuadCorner.BottomLeft,
];

/** The corners of a sheet as a list, in {@link CORNER_ORDER}. */
export function pointsOf(quad: QuadShape): Point[] {
  return CORNER_ORDER.map((corner) => quad[corner]);
}

/** Give the sheet that fills the whole image, which is what a page with no sheet found stands for. */
export function wholeImage(size: Size): QuadShape {
  return {
    topLeft: { x: 0, y: 0 },
    topRight: { x: size.width, y: 0 },
    bottomRight: { x: size.width, y: size.height },
    bottomLeft: { x: 0, y: size.height },
  };
}

/** The sign of the turn from the vector a to b to the vector b to c. */
function turn(a: Point, b: Point, c: Point): number {
  return Math.sign((b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x));
}

/** Tell whether the corners make a convex quadrilateral that keeps their order, with no corner on a line of the others. */
export function isConvex(quad: QuadShape): boolean {
  const points = pointsOf(quad);
  const turns = points.map((point, index) =>
    turn(
      point,
      points[(index + 1) % points.length] ?? point,
      points[(index + 2) % points.length] ?? point,
    ),
  );
  return turns.every((sign) => sign !== 0 && sign === turns[0]);
}

/**
 * Move one corner to a point.
 *
 * @param quad The sheet.
 * @param corner The corner to move.
 * @param to Where the pointer is, in the pixels of the image.
 * @param size The size of the image.
 * @returns The sheet with the corner on the image, or the sheet as it was when the move would fold it.
 */
export function moveCorner(quad: QuadShape, corner: QuadCorner, to: Point, size: Size): QuadShape {
  const moved = { ...quad, [corner]: clampToScan(to, size) };
  return isConvex(moved) ? moved : quad;
}

/**
 * Give the sheet to start the editor from: the corners the step found, else the whole image.
 *
 * @param result What the step found on the page, or null when it has not run.
 * @param size The size of the image the step read.
 */
export function quadOf(result: PageResult | null, size: Size | null): QuadShape {
  return result?.quad ?? wholeImage(size ?? { width: 1, height: 1 });
}
