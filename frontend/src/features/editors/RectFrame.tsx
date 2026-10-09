import { Rect } from 'react-konva';
import type { FigureState } from '@/api';
import { FIGURE_STYLE } from '@/features/editors/figure';
import { SQUARE_HANDLE } from '@/features/editors/handles';
import type { SceneMapping } from '@/features/editors/mapping';
import { HANDLE_ORDER, handlePoint, moveHandle, type RectHandle } from '@/features/editors/rect';
import type { SceneFrame } from '@/features/editors/scene';
import type { ShapeEditing } from '@/features/editors/shapeEditing';
import { dashed, type Point, pairOf, type RectShape } from '@/features/editors/shapes';

/**
 * The frame of the frame editor and of the margins editor: the outline of a rectangle with a handle on each corner and the
 * middle of each side to drag.
 *
 * The rectangle is dragged within the image and never gets thinner than a few pixels, as {@link moveHandle} keeps it. The
 * margins editor draws the border of the page round it through `outer`, and its own handles after it.
 */

const FRAME_WIDTH_PX = 2;

/** A rectangle on the screen, in screen pixels. */
export interface ScreenBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A frame as it stands on the screen: its box, and where each handle is. */
export interface RectLayout {
  box: ScreenBox;
  places: readonly { handle: RectHandle; at: Point }[];
}

/** Give the box on the screen of a rectangle of the edit's image. */
export function screenBox(mapping: SceneMapping, rect: RectShape): ScreenBox {
  const topLeft = mapping.toScreen({ x: rect.left, y: rect.top });
  const bottomRight = mapping.toScreen({ x: rect.left + rect.width, y: rect.top + rect.height });
  return {
    x: topLeft.x,
    y: topLeft.y,
    width: bottomRight.x - topLeft.x,
    height: bottomRight.y - topLeft.y,
  };
}

/** Give where the box and the handles of a frame stand on the screen. */
export function layoutOf(mapping: SceneMapping, rect: RectShape): RectLayout {
  return {
    box: screenBox(mapping, rect),
    places: HANDLE_ORDER.map((handle) => ({
      handle,
      at: mapping.toScreen(handlePoint(rect, handle)),
    })),
  };
}

/** Write a rectangle as the four rounded numbers an attribute holds: left, top, width, height. */
export function rectList(rect: RectShape): string {
  return [rect.left, rect.top, rect.width, rect.height].map(Math.round).join(',');
}

/** Give the attributes of the layer that say where the frame and its handles stand, which the scenarios read. */
export function rectData(
  figure: FigureState,
  rect: RectShape,
  layout: RectLayout,
): Record<string, string> {
  const data: Record<string, string> = { figure, rect: rectList(rect) };
  for (const { handle, at } of layout.places) {
    data[`handle-${dashed(handle)}`] = pairOf(at);
  }
  return data;
}

export function RectFrame({
  frame,
  layout,
  figure,
  editing,
  handleName,
  outer = null,
}: {
  frame: SceneFrame;
  layout: RectLayout;
  figure: FigureState;
  editing: ShapeEditing<RectShape>;
  /** The name of a handle for a reader who cannot see it, given the place of the handle in words. */
  handleName: (place: string) => string;
  /** The border drawn round the frame, behind it. */
  outer?: ScreenBox | null;
}): React.JSX.Element {
  const { stroke, dash } = FIGURE_STYLE[figure];
  const outline = { stroke, dash, strokeWidth: FRAME_WIDTH_PX, listening: false };
  return (
    <>
      {outer === null ? null : <Rect {...outer} {...outline} />}
      <Rect {...layout.box} {...outline} />
      {layout.places.map(({ handle, at }) => (
        <Rect
          key={handle}
          {...SQUARE_HANDLE}
          x={at.x}
          y={at.y}
          fill={stroke}
          name={handleName(dashed(handle).replace('-', ' '))}
          onDragMove={editing.drag(
            (current, to) => moveHandle(current, handle, to, frame.size),
            (next) => handlePoint(next, handle),
          )}
          onDragEnd={editing.release}
        />
      ))}
    </>
  );
}
