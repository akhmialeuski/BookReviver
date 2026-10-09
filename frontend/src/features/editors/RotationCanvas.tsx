import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect } from 'react';
import { Circle, Line, Text } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { FIGURE_STYLE } from '@/features/editors/figure';
import { HANDLE_STYLE } from '@/features/editors/handles';
import {
  ANGLE_LIMIT,
  AxisEnd,
  angleFromAxisPointer,
  axisEndAt,
  formatAngle,
  stepByKey,
  stepByWheel,
} from '@/features/editors/rotation';
import { useSceneFrame } from '@/features/editors/scene';
import { useShapeEditing } from '@/features/editors/shapeEditing';
import type { RotationShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The rotation editor over the page: the picture turns with the angle, so the lines of text can be brought level with the
 * grid the canvas draws over it, and the axis through the middle of the page has a round handle at each end to set the
 * angle by.
 *
 * A handle is dragged round the middle of the page, and the axis turns with it, in the colour of the state the shape is
 * in. `Alt` and the wheel change the angle by a tenth of a degree and the arrow keys by 0.05, and the save waits for a
 * pause so that a run of notches or presses is saved once. The picture is turned back when the editor closes, since the
 * step turns the page itself.
 */

const labels = MESSAGES.editors.rotation;

const AXIS_WIDTH_PX = 2;
const HANDLE_RADIUS_PX = 10;
const HANDLE_BORDER_PX = 3;
const HANDLE_FILL = '#ffffff';
/** How far a handle stands off the edge of the page. */
const HANDLE_GAP_PX = 28;
/** The room kept between a handle and the edge of the canvas. */
const EDGE_ROOM_PX = 4;
const LABEL_FONT_PX = 14;
const LABEL_RISE_PX = 32;
const LABEL_WIDTH_PX = 80;
const HALF = 2;

const ENDS = [AxisEnd.Left, AxisEnd.Right] as const;

export function RotationCanvas({
  scene,
  shape,
  size,
  figure,
  onChange,
  onCommit,
  onCommitLater,
}: CanvasProps<RotationShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const { stroke, dash } = FIGURE_STYLE[figure];
  const { latest, change, commitLater, release } = useShapeEditing(
    frame,
    shape,
    onChange,
    onCommit,
    onCommitLater,
  );

  // The picture is turned the way the step will turn it, counter-clockwise, while the view counts clockwise
  const { image, viewer } = scene;
  useEffect(() => {
    image.setRotation(-shape.degrees, true);
  }, [image, shape.degrees]);
  useEffect(
    () => () => {
      if (!viewer.isDestroyed()) {
        image.setRotation(0, true);
      }
    },
    [image, viewer],
  );

  const { mapping, viewRotation } = frame;
  const centre = mapping.toScreen({ x: frame.size.width / HALF, y: frame.size.height / HALF });
  const halfWidth = (frame.size.width * mapping.pixelLength()) / HALF;
  // A handle stands off the page, in the margin, unless the page fills the canvas and there is no margin to stand in
  const radius = Math.max(
    Math.min(halfWidth + HANDLE_GAP_PX, frame.stage.width / HALF - HANDLE_RADIUS_PX - EDGE_ROOM_PX),
    HANDLE_RADIUS_PX * HALF,
  );
  const handles = {
    [AxisEnd.Left]: axisEndAt(centre, radius, shape.degrees, viewRotation, AxisEnd.Left),
    [AxisEnd.Right]: axisEndAt(centre, radius, shape.degrees, viewRotation, AxisEnd.Right),
  };

  const drag = (end: AxisEnd) => (event: KonvaEventObject<DragEvent>) => {
    const degrees = angleFromAxisPointer(
      centre,
      { x: event.target.x(), y: event.target.y() },
      viewRotation,
      end,
    );
    change({ degrees });
    event.target.position(axisEndAt(centre, radius, degrees, viewRotation, end));
  };

  const turnTo = (degrees: number): void => {
    const next = { degrees };
    change(next);
    commitLater(next);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const degrees = stepByKey(latest.current.degrees, event.key, event.shiftKey);
    if (degrees !== null && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      turnTo(degrees);
    }
  };

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: -ANGLE_LIMIT,
        max: ANGLE_LIMIT,
        now: shape.degrees,
        text: MESSAGES.processing.thisPage.degrees(shape.degrees),
      }}
      data={{
        figure,
        degrees: String(shape.degrees),
        'handle-rotation': `${handles.right.x},${handles.right.y}`,
        'handle-rotation-left': `${handles.left.x},${handles.left.y}`,
      }}
      onKeyDown={onKeyDown}
      onAltWheel={(deltaY) => turnTo(stepByWheel(latest.current.degrees, deltaY))}
    >
      <Line
        points={[handles.left.x, handles.left.y, handles.right.x, handles.right.y]}
        stroke={stroke}
        dash={dash}
        strokeWidth={AXIS_WIDTH_PX}
        listening={false}
      />
      <Text
        x={handles.right.x - LABEL_WIDTH_PX / HALF}
        y={handles.right.y - LABEL_RISE_PX}
        width={LABEL_WIDTH_PX}
        align="center"
        text={`${formatAngle(shape.degrees)}°`}
        fontSize={LABEL_FONT_PX}
        fontStyle="bold"
        fill={stroke}
        listening={false}
      />
      {ENDS.map((end) => (
        <Circle
          key={end}
          x={handles[end].x}
          y={handles[end].y}
          radius={HANDLE_RADIUS_PX}
          {...HANDLE_STYLE}
          fill={HANDLE_FILL}
          stroke={stroke}
          strokeWidth={HANDLE_BORDER_PX}
          name={`${labels.handle} ${end}`}
          onDragMove={drag(end)}
          onDragEnd={release}
        />
      ))}
    </EditorLayer>
  );
}
