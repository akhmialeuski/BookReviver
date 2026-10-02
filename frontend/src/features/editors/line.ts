import type { LineShape, Point, Size } from '@/features/editors/shapes';
import type { PageResult } from '@/features/processing/results';

/**
 * The arithmetic of the split line: where it starts, how an end moves, and how the arrow keys nudge it.
 *
 * Everything is in the pixels of the scan, which is what the cut of the server reads. The server cannot cut along a
 * horizontal line, so an end never comes to the height of the other.
 */

/** Pixels an arrow key moves the line by, and with `Shift` held. */
export const NUDGE_PX = 1;
export const NUDGE_SHIFT_PX = 10;

const HALF = 2;

/** The least vertical distance between the two ends, in pixels. */
export const MIN_SPAN_PX = 1;

/** Which end of the line. */
export const LineEnd = { Start: 'start', End: 'end' } as const;

/** One end of the line (derived from {@link LineEnd}). */
export type LineEnd = (typeof LineEnd)[keyof typeof LineEnd];

function within(value: number, least: number, most: number): number {
  return Math.min(Math.max(value, least), most);
}

/** Give the vertical line at a distance from the left edge, from the top of the scan to its bottom. */
export function verticalLine(x: number, size: Size): LineShape {
  const at = within(x, 0, size.width);
  return { start: { x: at, y: 0 }, end: { x: at, y: size.height } };
}

/**
 * Give the line where a step cut a scan: the slanted line through the ends it reported, else the vertical line at its cut,
 * else the vertical line down the middle of the scan.
 *
 * @param result What the step found on the page, or null when it has not run.
 * @param size The size of the scan.
 */
export function cutLine(result: PageResult | null, size: Size): LineShape {
  const top = result?.cutTopX ?? null;
  const bottom = result?.cutBottomX ?? null;
  if (top !== null && bottom !== null) {
    return {
      start: clampToScan({ x: top, y: 0 }, size),
      end: clampToScan({ x: bottom, y: size.height }, size),
    };
  }
  return verticalLine(result?.cutX ?? size.width / HALF, size);
}

/** Keep a point on the scan. */
export function clampToScan(point: Point, size: Size): Point {
  return { x: within(point.x, 0, size.width), y: within(point.y, 0, size.height) };
}

/**
 * Move one end of the line to a point.
 *
 * @param line The line.
 * @param end The end to move.
 * @param to Where the pointer is, in scan pixels.
 * @param size The size of the scan.
 * @returns The line with the end on the scan, or the line as it was when the move would make it horizontal.
 */
export function moveEnd(line: LineShape, end: LineEnd, to: Point, size: Size): LineShape {
  const point = clampToScan(to, size);
  const other = end === LineEnd.Start ? line.end : line.start;
  if (Math.abs(point.y - other.y) < MIN_SPAN_PX) {
    return line;
  }
  return end === LineEnd.Start ? { ...line, start: point } : { ...line, end: point };
}

/**
 * Move the whole line, by as much of the step as keeps both ends on the scan.
 *
 * @param line The line.
 * @param dx Pixels to move to the right.
 * @param dy Pixels to move down.
 * @param size The size of the scan.
 */
export function nudgeLine(line: LineShape, dx: number, dy: number, size: Size): LineShape {
  const moveX = within(
    dx,
    -Math.min(line.start.x, line.end.x),
    size.width - Math.max(line.start.x, line.end.x),
  );
  const moveY = within(
    dy,
    -Math.min(line.start.y, line.end.y),
    size.height - Math.max(line.start.y, line.end.y),
  );
  return {
    start: { x: line.start.x + moveX, y: line.start.y + moveY },
    end: { x: line.end.x + moveX, y: line.end.y + moveY },
  };
}

/** The distance from the left edge at which the line crosses the middle of the scan's height. */
export function crossingX(line: LineShape, height: number): number {
  const span = line.end.y - line.start.y;
  if (span === 0) {
    return line.start.x;
  }
  const share = (height / 2 - line.start.y) / span;
  return line.start.x + share * (line.end.x - line.start.x);
}

/** How far an arrow key moves the line, in pixels. */
export function nudgeStep(shift: boolean): number {
  return shift ? NUDGE_SHIFT_PX : NUDGE_PX;
}

const ARROW_DIRECTION: Readonly<Record<string, Point>> = {
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
};

/**
 * Work out the move an arrow key asks of the line.
 *
 * @param key The `key` of the keyboard event.
 * @param shift Whether `Shift` is held.
 * @returns The move in scan pixels, or null when the key is not an arrow.
 */
export function nudgeOfKey(key: string, shift: boolean): Point | null {
  const direction = ARROW_DIRECTION[key];
  if (direction === undefined) {
    return null;
  }
  const step = nudgeStep(shift);
  return { x: direction.x * step, y: direction.y * step };
}
