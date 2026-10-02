import { clampToScan } from '@/features/editors/line';
import type { Point, RectShape, Size } from '@/features/editors/shapes';
import type { PageResult } from '@/features/processing/results';

/**
 * The arithmetic of the frame editor: the frame of the content, its eight handles, and how a handle moves it.
 *
 * Everything is in the pixels of the image the step reads, which is what the crop step of the server reads. The frame
 * stays on the image and never gets thinner than a few pixels.
 */

/** Which handle of the frame: the four corners and the middle of the four sides. */
export const RectHandle = {
  TopLeft: 'topLeft',
  Top: 'top',
  TopRight: 'topRight',
  Right: 'right',
  BottomRight: 'bottomRight',
  Bottom: 'bottom',
  BottomLeft: 'bottomLeft',
  Left: 'left',
} as const;

/** One handle of the frame (derived from {@link RectHandle}). */
export type RectHandle = (typeof RectHandle)[keyof typeof RectHandle];

/** The handles in the order round the frame from the top left. */
export const HANDLE_ORDER: readonly RectHandle[] = [
  RectHandle.TopLeft,
  RectHandle.Top,
  RectHandle.TopRight,
  RectHandle.Right,
  RectHandle.BottomRight,
  RectHandle.Bottom,
  RectHandle.BottomLeft,
  RectHandle.Left,
];

/** The least width and height of the frame, in pixels. */
export const MIN_SIDE_PX = 8;

/** The share of each side of the image that the frame leaves free when nothing is known of the content. */
const FREE_SHARE = 0.1;

const HALF = 2;

/** Which sides of the frame a handle moves. */
const MOVES: Readonly<
  Record<RectHandle, { left?: boolean; right?: boolean; top?: boolean; bottom?: boolean }>
> = {
  topLeft: { left: true, top: true },
  top: { top: true },
  topRight: { right: true, top: true },
  right: { right: true },
  bottomRight: { right: true, bottom: true },
  bottom: { bottom: true },
  bottomLeft: { left: true, bottom: true },
  left: { left: true },
};

function within(value: number, least: number, most: number): number {
  return Math.min(Math.max(value, least), most);
}

/** Give the place of a handle on the frame. */
export function handlePoint(rect: RectShape, handle: RectHandle): Point {
  const move = MOVES[handle];
  const x = move.left
    ? rect.left
    : move.right
      ? rect.left + rect.width
      : rect.left + rect.width / HALF;
  const y = move.top
    ? rect.top
    : move.bottom
      ? rect.top + rect.height
      : rect.top + rect.height / HALF;
  return { x, y };
}

/**
 * Move a handle to a point, which moves the sides the handle holds.
 *
 * @param rect The frame.
 * @param handle The handle to move.
 * @param to Where the pointer is, in the pixels of the image.
 * @param size The size of the image.
 * @returns The frame with its sides on the image and at least {@link MIN_SIDE_PX} apart.
 */
export function moveHandle(rect: RectShape, handle: RectHandle, to: Point, size: Size): RectShape {
  const point = clampToScan(to, size);
  const move = MOVES[handle];
  let { left } = rect;
  let { top } = rect;
  let right = rect.left + rect.width;
  let bottom = rect.top + rect.height;
  if (move.left) {
    left = within(point.x, 0, right - MIN_SIDE_PX);
  }
  if (move.right) {
    right = within(point.x, left + MIN_SIDE_PX, size.width);
  }
  if (move.top) {
    top = within(point.y, 0, bottom - MIN_SIDE_PX);
  }
  if (move.bottom) {
    bottom = within(point.y, top + MIN_SIDE_PX, size.height);
  }
  return { left, top, width: right - left, height: bottom - top };
}

/**
 * Move the whole frame, by as much of the step as keeps it on the image.
 *
 * @param rect The frame.
 * @param dx Pixels to move to the right.
 * @param dy Pixels to move down.
 * @param size The size of the image.
 */
export function nudgeRect(rect: RectShape, dx: number, dy: number, size: Size): RectShape {
  return {
    ...rect,
    left: within(rect.left + dx, 0, size.width - rect.width),
    top: within(rect.top + dy, 0, size.height - rect.height),
  };
}

/**
 * Give the frame to start the editor from: the frame the step found, else the image less a free margin.
 *
 * @param result What the step found on the page, or null when it has not run.
 * @param size The size of the image the step read.
 */
export function rectOf(result: PageResult | null, size: Size | null): RectShape {
  const found = result?.frame ?? null;
  if (found !== null) {
    return found;
  }
  const { width, height } = size ?? { width: 1, height: 1 };
  return {
    left: width * FREE_SHARE,
    top: height * FREE_SHARE,
    width: width * (1 - HALF * FREE_SHARE),
    height: height * (1 - HALF * FREE_SHARE),
  };
}
