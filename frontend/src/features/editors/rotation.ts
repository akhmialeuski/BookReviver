import type { Point } from '@/features/editors/shapes';

/**
 * The arithmetic of the rotation handles: the angle a pointer asks for, where a handle stands for an angle, and the
 * steps of the field, the wheel and the keys.
 *
 * An angle is in degrees, counter-clockwise, as the server turns the page. On the screen an angle of zero lays the axis
 * level, with one handle at its right end and one at its left.
 */

/** The step of the wheel with `Alt`, and of the field, in degrees. */
export const ANGLE_STEP = 0.1;

/** The step of an arrow key, in degrees, which is finer than the wheel and the field. */
export const ANGLE_KEY_STEP = 0.05;

/** The step of an arrow key held with `Shift`, in degrees. */
export const ANGLE_STEP_SHIFT = 1;

/** The end of the axis a handle stands at. */
export const AxisEnd = {
  Left: 'left',
  Right: 'right',
} as const;

/** One end of the axis (derived from {@link AxisEnd}). */
export type AxisEnd = (typeof AxisEnd)[keyof typeof AxisEnd];

/** How far clockwise from straight up each end of the axis lies when the angle is zero, in degrees. */
const END_BEARING: Readonly<Record<AxisEnd, number>> = {
  [AxisEnd.Right]: 90,
  [AxisEnd.Left]: 270,
};

/** The largest angle the editor accepts, each way. */
export const ANGLE_LIMIT = 45;

const HALF_TURN = 180;
const FULL_TURN = 360;
const TENTHS = 10;
const HUNDREDTHS = 100;

/** Round an angle to a tenth of a degree. */
export function roundAngle(degrees: number): number {
  return Math.round(degrees * TENTHS) / TENTHS;
}

/** Round an angle to a tenth of a degree and keep it within the limit. */
export function limitAngle(degrees: number): number {
  const rounded = roundAngle(degrees);
  return Math.min(Math.max(rounded, -ANGLE_LIMIT), ANGLE_LIMIT);
}

/** Round an angle to a hundredth of a degree and keep it within the limit, as the arrow keys step it. */
export function limitFineAngle(degrees: number): number {
  const rounded = Math.round(degrees * HUNDREDTHS) / HUNDREDTHS;
  return Math.min(Math.max(rounded, -ANGLE_LIMIT), ANGLE_LIMIT);
}

/** Write an angle for the field and the label of the axis: up to a hundredth of a degree, with no trailing zeros. */
export function formatAngle(degrees: number): string {
  return String(Number(degrees.toFixed(2)));
}

/** Turn an angle into the range from just over -180 to 180 degrees. */
function wrapAngle(degrees: number): number {
  const wrapped = ((((degrees + HALF_TURN) % FULL_TURN) + FULL_TURN) % FULL_TURN) - HALF_TURN;
  return wrapped === -HALF_TURN ? HALF_TURN : wrapped;
}

/**
 * Work out the angle a pointer asks for.
 *
 * @param centre The middle of the page on the screen.
 * @param pointer The pointer on the screen.
 * @param viewRotation How far the view is turned clockwise, in degrees.
 * @returns The angle, limited and rounded to a tenth of a degree.
 */
export function angleFromPointer(centre: Point, pointer: Point, viewRotation: number): number {
  return limitAngle(wrapAngle(viewRotation - clockwiseOf(centre, pointer)));
}

/** Work out how far clockwise from straight up a point of the screen lies from a centre, in degrees. */
function clockwiseOf(centre: Point, pointer: Point): number {
  return (Math.atan2(pointer.x - centre.x, centre.y - pointer.y) * HALF_TURN) / Math.PI;
}

/**
 * Work out the angle a pointer asks for when it drags one end of the axis.
 *
 * @param centre The middle of the page on the screen.
 * @param pointer The pointer on the screen.
 * @param viewRotation How far the view is turned clockwise, in degrees.
 * @param end The end of the axis the pointer holds.
 * @returns The angle, limited and rounded to a tenth of a degree.
 */
export function angleFromAxisPointer(
  centre: Point,
  pointer: Point,
  viewRotation: number,
  end: AxisEnd,
): number {
  return limitAngle(wrapAngle(viewRotation - clockwiseOf(centre, pointer) + END_BEARING[end]));
}

/**
 * Work out where one end of the axis stands for an angle.
 *
 * @param centre The middle of the page on the screen.
 * @param radius The distance of the end from the middle, in screen pixels.
 * @param degrees The angle.
 * @param viewRotation How far the view is turned clockwise, in degrees.
 * @param end The end of the axis.
 */
export function axisEndAt(
  centre: Point,
  radius: number,
  degrees: number,
  viewRotation: number,
  end: AxisEnd,
): Point {
  return handleAt(centre, radius, degrees - END_BEARING[end], viewRotation);
}

/**
 * Work out where the handle stands for an angle.
 *
 * @param centre The middle of the page on the screen.
 * @param radius The distance of the handle from the middle, in screen pixels.
 * @param degrees The angle.
 * @param viewRotation How far the view is turned clockwise, in degrees.
 */
export function handleAt(
  centre: Point,
  radius: number,
  degrees: number,
  viewRotation: number,
): Point {
  const clockwise = ((viewRotation - degrees) * Math.PI) / HALF_TURN;
  return {
    x: centre.x + radius * Math.sin(clockwise),
    y: centre.y - radius * Math.cos(clockwise),
  };
}

/**
 * Step an angle by a notch of the wheel.
 *
 * @param degrees The angle now.
 * @param deltaY The `deltaY` of the wheel event; a turn away from the reader, which is negative, adds to the angle.
 */
export function stepByWheel(degrees: number, deltaY: number): number {
  if (deltaY === 0) {
    return degrees;
  }
  return limitAngle(degrees + (deltaY < 0 ? ANGLE_STEP : -ANGLE_STEP));
}

const KEY_DIRECTION: Readonly<Record<string, number>> = {
  ArrowRight: 1,
  ArrowUp: 1,
  ArrowLeft: -1,
  ArrowDown: -1,
};

/**
 * Step an angle by an arrow key, as a slider does: right and up add to the angle, left and down take from it.
 *
 * @param degrees The angle now.
 * @param key The `key` of the keyboard event.
 * @param shift Whether `Shift` is held, which steps by a whole degree, and not by {@link ANGLE_KEY_STEP}.
 * @returns The new angle, or null when the key is not an arrow.
 */
export function stepByKey(degrees: number, key: string, shift: boolean): number | null {
  const direction = KEY_DIRECTION[key];
  if (direction === undefined) {
    return null;
  }
  return limitFineAngle(degrees + direction * (shift ? ANGLE_STEP_SHIFT : ANGLE_KEY_STEP));
}

/**
 * Read the text of the angle field.
 *
 * @param text What the reader typed.
 * @returns The angle limited and rounded, or null when the text is not a number.
 */
export function parseAngle(text: string): number | null {
  const trimmed = text.trim().replace(',', '.');
  if (trimmed === '') {
    return null;
  }
  const value = Number(trimmed);
  return Number.isFinite(value) ? limitAngle(value) : null;
}
