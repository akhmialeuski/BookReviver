/**
 * The grid over the page of a Geometry step: lines straight across and straight down the canvas, at an even spacing, which
 * take no pointer so the page and its shapes are worked on through them.
 *
 * It stands level with the screen at every step, so a line of text or an edge of the block that is crooked shows against
 * it at once. At the Deskew step the picture turns under it as the angle changes.
 */

/** The distance between two lines, in screen pixels. */
const GRID_STEP_PX = 48;
const LINE_COLOR = 'rgba(37, 99, 235, 0.28)';
const LINE_WIDTH_PX = 1;

const LINE = `${LINE_COLOR} ${LINE_WIDTH_PX}px, transparent ${LINE_WIDTH_PX}px`;

export function GridOverlay(): React.JSX.Element {
  return (
    <div
      aria-hidden="true"
      data-testid="canvas-grid"
      className="pointer-events-none absolute inset-0 z-10"
      style={{
        backgroundImage: `linear-gradient(to right, ${LINE}), linear-gradient(to bottom, ${LINE})`,
        backgroundSize: `${GRID_STEP_PX}px ${GRID_STEP_PX}px`,
      }}
    />
  );
}
