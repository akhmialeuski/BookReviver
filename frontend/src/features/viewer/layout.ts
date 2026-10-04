/**
 * Where the pages of one view sit in the coordinates of the OpenSeadragon world, and which rectangle a zoom fits.
 *
 * The world is measured in page heights: every page is one unit tall and as wide as its aspect ratio makes it, and
 * the pages of a spread stand side by side from x = 0 with a narrow gutter between them. Keeping this geometry out of
 * the viewer class makes it testable without a browser.
 */

/** Height of every page in the world. */
export const PAGE_HEIGHT = 1;

/** Space between the two pages of a spread, in page heights. */
export const SPREAD_GUTTER = 0.02;

/** Aspect ratio, width over height, of a page whose image is missing or whose size is not known. */
export const FALLBACK_ASPECT = 0.7;

/** Where one page of a view stands. */
export interface PlacedPage {
  x: number;
  width: number;
}

/** The pages of one view placed side by side, and the size of the whole view. */
export interface ViewLayout {
  pages: PlacedPage[];
  width: number;
  height: number;
}

/** A rectangle of the world, in the same units as `OpenSeadragon.Rect`. */
export interface WorldRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Place pages side by side, left to right, each one page tall.
 *
 * @param aspects Width over height of each page; a value that is not a positive finite number is replaced by
 * `FALLBACK_ASPECT`, so a page without an image still takes a slot.
 */
export function layoutView(aspects: readonly number[]): ViewLayout {
  let x = 0;
  const pages = aspects.map((aspect, index) => {
    const width = (Number.isFinite(aspect) && aspect > 0 ? aspect : FALLBACK_ASPECT) * PAGE_HEIGHT;
    const placed = { x: index === 0 ? 0 : x + SPREAD_GUTTER, width };
    x = placed.x + width;
    return placed;
  });
  return { pages, width: x, height: PAGE_HEIGHT };
}

/**
 * The height in pixels the floating toolbar at the bottom of the canvas covers, counted from the bottom edge of the
 * canvas: its own height and the gap under it. A page fitted whole ends above it, so nothing at the bottom edge of the
 * page, such as a handle of an editor, lies under the toolbar.
 */
export const TOOLBAR_INSET_PX = 64;

/**
 * Grow a rectangle downward so that, when it is fitted to a viewport, what it holds ends a number of pixels above the
 * bottom of the viewport.
 *
 * The zoom of the fit is the smaller of the zoom that fills the width and the one that fills the height left over, and
 * the rectangle gets the world distance that many pixels span at that zoom, below its own bottom edge.
 *
 * @param rect The rectangle that is to be fully shown.
 * @param viewport The size of the viewport in pixels.
 * @param bottomPx The pixels to keep free at the bottom of the viewport.
 * @returns The rectangle with the room below it, or the rectangle itself while the viewport has no room to spare.
 */
export function withBottomInset(
  rect: WorldRect,
  viewport: { width: number; height: number },
  bottomPx: number,
): WorldRect {
  const room = viewport.height - bottomPx;
  if (rect.width <= 0 || rect.height <= 0 || viewport.width <= 0 || room <= 0) {
    return rect;
  }
  const scale = Math.min(viewport.width / rect.width, room / rect.height);
  return { ...rect, height: rect.height + bottomPx / scale };
}

/** The rectangle that shows the whole view, which `fit to page` zooms to. */
export function pageRect(layout: ViewLayout): WorldRect {
  return { x: 0, y: 0, width: layout.width, height: layout.height };
}

/**
 * The rectangle that fills the width of the viewport with the view, from its top edge down.
 *
 * @param layout The view to fit.
 * @param viewportAspect Width over height of the viewport, so the rectangle has the viewport's shape and the zoom
 * is exactly the one at which the view's width spans the viewport.
 */
export function widthRect(layout: ViewLayout, viewportAspect: number): WorldRect {
  const aspect = Number.isFinite(viewportAspect) && viewportAspect > 0 ? viewportAspect : 1;
  return { x: 0, y: 0, width: layout.width, height: layout.width / aspect };
}
