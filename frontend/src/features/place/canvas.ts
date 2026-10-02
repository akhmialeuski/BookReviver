import type { CanvasPositionSchema } from '@/api';

/**
 * The position of a canvas in terms that do not depend on the size of the window.
 *
 * OpenSeadragon counts its zoom as the reciprocal of the width of the world it shows, so the same zoom shows a
 * different part of a page on a phone and on a wide screen. A place is read on another device, so it keeps the zoom as
 * a multiple of the zoom that fits the whole view into the canvas, where 1 is the fitted view, and the centre in page
 * heights, which the layout of the view fixes. These functions convert between the two.
 */

/** Decimal digits a stored position keeps, which stops a restored view from differing from the stored one by rounding. */
const POSITION_DIGITS = 4;
const POSITION_SCALE = 10 ** POSITION_DIGITS;

/** The size of the pages of a view in the world, in page heights. */
export interface ViewSize {
  width: number;
  height: number;
}

/** A point of the world, in page heights. */
export interface WorldPoint {
  x: number;
  y: number;
}

function rounded(value: number): number {
  return Math.round(value * POSITION_SCALE) / POSITION_SCALE;
}

/**
 * Give the width of the world that a viewport shows when the whole view is fitted into it.
 *
 * @param view The pages of the view.
 * @param viewportAspect Width over height of the viewport; a value that is not a positive finite number counts as 1.
 */
export function fittedWidth(view: ViewSize, viewportAspect: number): number {
  const aspect = Number.isFinite(viewportAspect) && viewportAspect > 0 ? viewportAspect : 1;
  return Math.max(view.width, view.height * aspect);
}

/**
 * Describe where a canvas looks.
 *
 * @param zoom The zoom of the viewport, which is the reciprocal of the width of the world it shows.
 * @param centre The centre of the viewport in the world.
 * @param fitted The width of the world the viewport shows when the view is fitted, from {@link fittedWidth}.
 * @returns The position, or null when a number is not finite or the zoom is not above zero.
 */
export function positionOf(
  zoom: number,
  centre: WorldPoint,
  fitted: number,
): CanvasPositionSchema | null {
  const share = zoom * fitted;
  if (![share, centre.x, centre.y].every(Number.isFinite) || share <= 0) {
    return null;
  }
  return { zoom: rounded(share), centre_x: rounded(centre.x), centre_y: rounded(centre.y) };
}

/**
 * Give the zoom of the viewport that shows a stored position.
 *
 * @param position The stored position.
 * @param fitted The width of the world the viewport shows when the view is fitted, from {@link fittedWidth}.
 */
export function viewportZoom(position: CanvasPositionSchema, fitted: number): number {
  return position.zoom / fitted;
}
