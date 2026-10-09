/**
 * The look every draggable handle of an editor shares: a white border, a wide grab area round the shape and the
 * `draggable` flag. Each canvas spreads one of these into its Konva handle and adds what is its own, such as the fill,
 * the name and the drag callbacks.
 */

const BORDER_PX = 2;
/** How far beyond its drawn edge a handle can be grabbed. */
const HIT_EXTRA_PX = 8;
const SQUARE_SIDE_PX = 12;
const HALF = 2;

export const HANDLE_STYLE = {
  stroke: '#ffffff',
  strokeWidth: BORDER_PX,
  hitStrokeWidth: HIT_EXTRA_PX,
  draggable: true,
} as const;

/** A square handle that stands centred on its place, such as a corner or the middle of a side of a frame. */
export const SQUARE_HANDLE = {
  ...HANDLE_STYLE,
  width: SQUARE_SIDE_PX,
  height: SQUARE_SIDE_PX,
  offsetX: SQUARE_SIDE_PX / HALF,
  offsetY: SQUARE_SIDE_PX / HALF,
} as const;

/** A square handle with round corners, which tells a handle that moves a margin from one that moves the frame. */
export const ROUND_SQUARE_HANDLE = {
  ...SQUARE_HANDLE,
  cornerRadius: SQUARE_SIDE_PX / HALF,
} as const;
