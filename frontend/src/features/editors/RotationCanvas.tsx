import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef } from 'react';
import { Circle, Line } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import {
  ANGLE_LIMIT,
  angleFromPointer,
  handleAt,
  stepByKey,
  stepByWheel,
} from '@/features/editors/rotation';
import { useSceneFrame } from '@/features/editors/scene';
import type { RotationShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';
import { MESSAGES } from '@/shared/messages';

/**
 * The rotation editor over the page: the picture turns with the angle, so the lines of text can be brought level with the
 * guides drawn over it, and a handle on the edge of the page sets the angle.
 *
 * The handle is dragged round the middle of the page. `Alt` and the wheel, or the arrow keys, change the angle by a tenth
 * of a degree, and the save waits for a pause so that a run of notches or presses is saved once. The picture is turned back when the
 * editor closes, since the step turns the page itself.
 */

const labels = MESSAGES.editors.rotation;

const GUIDE_COLOR = 'rgba(14, 165, 233, 0.5)';
const GUIDE_WIDTH_PX = 1;
/** How many bands the guides divide the page into. */
const GUIDE_BANDS = 24;
const AXIS_COLOR = '#0ea5e9';
const AXIS_WIDTH_PX = 1;
const AXIS_DASH_PX = [4, 4];
const HANDLE_RADIUS_PX = 10;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
/** How far the handle stands off the edge of the page. */
const HANDLE_GAP_PX = 28;
/** Quiet time after the last notch of the wheel before the angle is saved. */
const WHEEL_SAVE_DELAY_MS = 600;
const HALF = 2;

export function RotationCanvas({
  scene,
  shape,
  size,
  onChange,
  onCommit,
}: CanvasProps<RotationShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const saveLater = useDebouncedCommit(onCommit, WHEEL_SAVE_DELAY_MS);
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);

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
  const half = {
    width: (frame.size.width * mapping.pixelLength()) / HALF,
    height: (frame.size.height * mapping.pixelLength()) / HALF,
  };
  const radius = half.height + HANDLE_GAP_PX;
  const handle = handleAt(centre, radius, shape.degrees, viewRotation);

  const guides = Array.from({ length: GUIDE_BANDS - 1 }, (_, index) => {
    const y = centre.y - half.height + ((index + 1) * 2 * half.height) / GUIDE_BANDS;
    return [centre.x - half.width, y, centre.x + half.width, y];
  });

  const drag = (event: KonvaEventObject<DragEvent>) => {
    const degrees = angleFromPointer(
      centre,
      { x: event.target.x(), y: event.target.y() },
      viewRotation,
    );
    const next = { degrees };
    latest.current = next;
    onChange(next);
    event.target.position(handleAt(centre, radius, degrees, viewRotation));
  };

  const turnTo = (degrees: number): void => {
    const next = { degrees };
    latest.current = next;
    onChange(next);
    saveLater(next);
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
      data={{ degrees: String(shape.degrees), 'handle-rotation': `${handle.x},${handle.y}` }}
      onKeyDown={onKeyDown}
      onAltWheel={(deltaY) => turnTo(stepByWheel(latest.current.degrees, deltaY))}
    >
      {guides.map((points) => (
        <Line
          key={points[1]}
          points={points}
          stroke={GUIDE_COLOR}
          strokeWidth={GUIDE_WIDTH_PX}
          listening={false}
        />
      ))}
      <Line
        points={[centre.x, centre.y, handle.x, handle.y]}
        stroke={AXIS_COLOR}
        strokeWidth={AXIS_WIDTH_PX}
        dash={AXIS_DASH_PX}
        listening={false}
      />
      <Circle
        x={handle.x}
        y={handle.y}
        radius={HANDLE_RADIUS_PX}
        fill={AXIS_COLOR}
        stroke={HANDLE_BORDER_COLOR}
        strokeWidth={HANDLE_BORDER_PX}
        hitStrokeWidth={HIT_EXTRA_PX}
        draggable
        name={labels.handle}
        onDragMove={drag}
        onDragEnd={() => onCommit(latest.current)}
      />
    </EditorLayer>
  );
}
