import { useState } from 'react';
import { Circle, Line } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { FIGURE_STYLE } from '@/features/editors/figure';
import { HANDLE_STYLE } from '@/features/editors/handles';
import { CORNER_ORDER, moveCorner, QuadCorner } from '@/features/editors/quad';
import { useSceneFrame } from '@/features/editors/scene';
import { useShapeEditing, widthValue } from '@/features/editors/shapeEditing';
import { dashed, pairOf, type QuadShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
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

export function QuadCanvas({
  scene,
  shape,
  size,
  figure,
  onChange,
  onCommit,
  onCommitLater,
}: CanvasProps<QuadShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const { stroke, dash } = FIGURE_STYLE[figure];
  const editing = useShapeEditing(frame, shape, onChange, onCommit, onCommitLater);
  const [grabbed, setGrabbed] = useState<QuadCorner>(QuadCorner.TopLeft);

  const { mapping } = frame;
  const screen = CORNER_ORDER.map((corner) => mapping.toScreen(shape[corner]));

  const onKeyDown = editing.onKeyDown((current, step) => {
    const from = current[grabbed];
    return moveCorner(current, grabbed, { x: from.x + step.x, y: from.y + step.y }, frame.size);
  });

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
      value={widthValue(frame, width)}
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
          {...HANDLE_STYLE}
          radius={HANDLE_RADIUS_PX}
          fill={stroke}
          name={labels.corner(dashed(corner).replace('-', ' '))}
          onDragMove={editing.drag(
            (current, at) => moveCorner(current, corner, at, frame.size),
            (next) => next[corner],
            () => setGrabbed(corner),
          )}
          onDragEnd={editing.release}
        />
      ))}
    </EditorLayer>
  );
}
