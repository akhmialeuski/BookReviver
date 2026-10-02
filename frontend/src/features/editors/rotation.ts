import type { Point } from '@/features/editors/shapes';

/**
 * The arithmetic of the rotation handle: the angle a pointer asks for, where the handle stands for an angle, and the
 * steps of the field and of the wheel.
 *
 * An angle is in degrees, counter-clockwise, as the server turns the page. On the screen an angle of zero puts the handle
 * straight above the middle of the page.
 */

/** The step of the wheel with `Alt`, and of the field, in degrees. */
export const ANGLE_STEP = 0.1;

/** The step of an arrow key held with `Shift`, in degrees. */
export const ANGLE_STEP_SHIFT = 1;

/** The largest angle the editor accepts, each way. */
export const ANGLE_LIMIT = 45;

const HALF_TURN = 180;
const FULL_TURN = 360;
const TENTHS = 10;

/** Round an angle to a tenth of a degree. */
export function roundAngle(degrees: number): number {
  return Math.round(degrees * TENTHS) / TENTHS;
}

/** Round an angle to a tenth of a degree and keep it within the limit. */
export function limitAngle(degrees: number): number {
  const rounded = roundAngle(degrees);
  return Math.min(Math.max(rounded, -ANGLE_LIMIT), ANGLE_LIMIT);
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
  const clockwise = (Math.atan2(pointer.x - centre.x, centre.y - pointer.y) * HALF_TURN) / Math.PI;
  return limitAngle(wrapAngle(viewRotation - clockwise));
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
 * @param shift Whether `Shift` is held, which steps by a whole degree.
 * @returns The new angle, or null when the key is not an arrow.
 */
export function stepByKey(degrees: number, key: string, shift: boolean): number | null {
  const direction = KEY_DIRECTION[key];
  if (direction === undefined) {
    return null;
  }
  return limitAngle(degrees + direction * (shift ? ANGLE_STEP_SHIFT : ANGLE_STEP));
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
