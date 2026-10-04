import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef, useState } from 'react';
import { Circle, Line } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { FIGURE_STYLE } from '@/features/editors/figure';
import { nudgeOfKey } from '@/features/editors/line';
import { CORNER_ORDER, moveCorner, QuadCorner } from '@/features/editors/quad';
import { useSceneFrame } from '@/features/editors/scene';
import { dashed, type Point, type QuadShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';
import { MESSAGES } from '@/shared/messages';

/**
 * The sheet over the picture: the outline of the four corners of the paper, with a handle on each to drag.
 *
 * A corner is saved when it is let go. The arrow keys move the corner that was grabbed last by a pixel, or by ten with
 * Shift, and the save waits for a pause in the keys so that a run of presses is saved once.
 */

const labels = MESSAGES.editors.quad;

const SHEET_WIDTH_PX = 2;
const HANDLE_RADIUS_PX = 9;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
/** Quiet time after the last key before the corners are saved. */
const KEY_SAVE_DELAY_MS = 600;

function pairOf(point: Point): string {
  return `${Math.round(point.x)},${Math.round(point.y)}`;
}

export function QuadCanvas({
  scene,
  shape,
  size,
  figure,
  onChange,
  onCommit,
}: CanvasProps<QuadShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const { stroke, dash } = FIGURE_STYLE[figure];
  const saveLater = useDebouncedCommit(onCommit, KEY_SAVE_DELAY_MS);
  // The drag handlers run between renders, so the shape they build on is the latest one and not the one they closed over
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);
  const [grabbed, setGrabbed] = useState<QuadCorner>(QuadCorner.TopLeft);

  const { mapping } = frame;
  const screen = CORNER_ORDER.map((corner) => mapping.toScreen(shape[corner]));

  const drag = (corner: QuadCorner) => (event: KonvaEventObject<DragEvent>) => {
    setGrabbed(corner);
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = moveCorner(latest.current, corner, at, frame.size);
    latest.current = next;
    onChange(next);
    // The corner follows the pointer only as far as the sheet may go
    event.target.position(mapping.toScreen(next[corner]));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const step = nudgeOfKey(event.key, event.shiftKey);
    if (step === null || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    event.preventDefault();
    const from = latest.current[grabbed];
    const next = moveCorner(
      latest.current,
      grabbed,
      { x: from.x + step.x, y: from.y + step.y },
      frame.size,
    );
    latest.current = next;
    onChange(next);
    saveLater(next);
  };

  const data: Record<string, string> = { figure };
  CORNER_ORDER.forEach((corner, index) => {
    data[`corner-${dashed(corner)}`] = pairOf(shape[corner]);
    data[`handle-${dashed(corner)}`] = pairOf(screen[index] ?? { x: 0, y: 0 });
  });
  const width = Math.hypot(shape.topRight.x - shape.topLeft.x, shape.topRight.y - shape.topLeft.y);

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: 0,
        max: frame.size.width,
        now: Math.round(width),
        text: MESSAGES.processing.thisPage.pixels(width),
      }}
      data={data}
      onKeyDown={onKeyDown}
    >
      <Line
        points={screen.flatMap((point) => [point.x, point.y])}
        closed
        stroke={stroke}
        dash={dash}
        strokeWidth={SHEET_WIDTH_PX}
        listening={false}
      />
      {CORNER_ORDER.map((corner, index) => (
        <Circle
          key={corner}
          x={screen[index]?.x ?? 0}
          y={screen[index]?.y ?? 0}
          radius={HANDLE_RADIUS_PX}
          fill={stroke}
          stroke={HANDLE_BORDER_COLOR}
          strokeWidth={HANDLE_BORDER_PX}
          hitStrokeWidth={HIT_EXTRA_PX}
          draggable
          name={labels.corner(dashed(corner).replace('-', ' '))}
          onDragMove={drag(corner)}
          onDragEnd={() => onCommit(latest.current)}
        />
      ))}
    </EditorLayer>
  );
}
