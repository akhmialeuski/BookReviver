import { rectOf } from '@/features/editors/rect';
import type { Point, RectShape, Size } from '@/features/editors/shapes';
import { MARGIN_SIDES, type MarginSide, type PageResult } from '@/features/processing/results';

/**
 * The arithmetic of the Margins editor: the box of the content, the border of the page grown from it by the margins, and how
 * a side of the border sets the margin of that side.
 *
 * Everything here is in the pixels of the picture the step reads. The margins of the page are set in the pixels of the page,
 * where the box was scaled by the factor the step recorded, so a distance on the picture is multiplied by that factor to be a
 * setting. The step says which setting holds the margin of each side, since that depends on the side of the book.
 */

/** The distance of each side of the border from the box, in the pixels of the picture. */
export type Margins = Readonly<Record<MarginSide, number>>;

/**
 * Read the margins the step found, as the distance of the border it recorded from the box it recorded.
 *
 * @param result What the step found on the page.
 * @returns The margins, or null when the step did not record both boxes.
 */
export function marginsOf(result: PageResult | null): Margins | null {
  const box = result?.contentBox ?? null;
  const border = result?.marginBox ?? null;
  if (box === null || border === null) {
    return null;
  }
  return {
    left: box.left - border.left,
    top: box.top - border.top,
    right: border.left + border.width - (box.left + box.width),
    bottom: border.top + border.height - (box.top + box.height),
  };
}

/** Grow the box by the margins, which is the border of the page. */
export function outerOf(box: RectShape, margins: Margins): RectShape {
  return {
    left: box.left - margins.left,
    top: box.top - margins.top,
    width: box.width + margins.left + margins.right,
    height: box.height + margins.top + margins.bottom,
  };
}

/** Give the place of the middle of a side of a rectangle. */
export function sidePoint(rect: RectShape, side: MarginSide): Point {
  switch (side) {
    case 'left':
      return { x: rect.left, y: rect.top + rect.height / 2 };
    case 'right':
      return { x: rect.left + rect.width, y: rect.top + rect.height / 2 };
    case 'top':
      return { x: rect.left + rect.width / 2, y: rect.top };
    case 'bottom':
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height };
  }
}

/**
 * Give the margin a side has when it is dragged to a point: the distance of the point from that side of the box, which is
 * never less than none, since the border stays outside the box.
 */
export function marginAt(box: RectShape, side: MarginSide, to: Point): number {
  switch (side) {
    case 'left':
      return Math.max(box.left - to.x, 0);
    case 'right':
      return Math.max(to.x - (box.left + box.width), 0);
    case 'top':
      return Math.max(box.top - to.y, 0);
    case 'bottom':
      return Math.max(to.y - (box.top + box.height), 0);
  }
}

/** Turn a margin on the picture into the setting of the page, in the pixels of the page, as a whole number. */
export function settingOf(margin: number, blockScale: number): number {
  return Math.round(margin * blockScale);
}

/** Give the margins of the sides a drag has moved over the ones the step found. */
export function withMargin(margins: Margins, side: MarginSide, value: number): Margins {
  return { ...margins, [side]: value };
}

/** Tell whether the margins of two borders are the same, to the pixel. */
export function sameMargins(a: Margins, b: Margins): boolean {
  return MARGIN_SIDES.every((side) => Math.round(a[side]) === Math.round(b[side]));
}

/**
 * Give the box to start the editor from: the box the step found, else the picture less a free margin.
 *
 * @param result What the step found on the page, or null when it has not run.
 * @param size The size of the picture the step reads.
 */
export function contentBoxOf(result: PageResult | null, size: Size | null): RectShape {
  return result?.contentBox ?? rectOf(null, size);
}
