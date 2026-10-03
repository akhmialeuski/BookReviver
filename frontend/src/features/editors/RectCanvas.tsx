import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef } from 'react';
import { Rect } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { nudgeOfKey } from '@/features/editors/line';
import { isPlacement } from '@/features/editors/placement';
import {
  HANDLE_ORDER,
  handlePoint,
  moveHandle,
  nudgeRect,
  type RectHandle,
} from '@/features/editors/rect';
import { useSceneFrame } from '@/features/editors/scene';
import { dashed, type Point, type RectShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';
import { MESSAGES } from '@/shared/messages';

/**
 * The frame over the picture: the outline of the content of the page, with a handle on each corner and the middle of each
 * side to drag.
 *
 * A handle is saved when it is let go. The arrow keys move the whole frame by a pixel, or by ten with Shift, and the save
 * waits for a pause in the keys so that a run of presses is saved once.
 */

const labels = MESSAGES.editors.rect;

const FRAME_COLOR = '#2563eb';
const FRAME_WIDTH_PX = 2;
const HANDLE_SIDE_PX = 12;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
/** Quiet time after the last key before the frame is saved. */
const KEY_SAVE_DELAY_MS = 600;
const HALF = 2;

function pairOf(point: Point): string {
  return `${Math.round(point.x)},${Math.round(point.y)}`;
}

export function RectCanvas({
  scene,
  shape,
  size,
  context,
  onChange,
  onCommit,
}: CanvasProps<RectShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const saveLater = useDebouncedCommit(onCommit, KEY_SAVE_DELAY_MS);
  // The drag handlers run between renders, so the shape they build on is the latest one and not the one they closed over
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);

  const { mapping } = frame;
  const topLeft = mapping.toScreen({ x: shape.left, y: shape.top });
  const bottomRight = mapping.toScreen({
    x: shape.left + shape.width,
    y: shape.top + shape.height,
  });
  const places = HANDLE_ORDER.map((handle) => ({
    handle,
    at: mapping.toScreen(handlePoint(shape, handle)),
  }));

  const drag = (handle: RectHandle) => (event: KonvaEventObject<DragEvent>) => {
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = moveHandle(latest.current, handle, at, frame.size);
    latest.current = next;
    onChange(next);
    // The handle follows the pointer only as far as the frame may go
    event.target.position(mapping.toScreen(handlePoint(next, handle)));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const step = nudgeOfKey(event.key, event.shiftKey);
    if (step === null || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    event.preventDefault();
    const next = nudgeRect(latest.current, step.x, step.y, frame.size);
    latest.current = next;
    onChange(next);
    saveLater(next);
  };

  const data: Record<string, string> = {
    rect: [shape.left, shape.top, shape.width, shape.height].map(Math.round).join(','),
  };
  for (const { handle, at } of places) {
    data[`handle-${dashed(handle)}`] = pairOf(at);
  }

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={isPlacement(context.processorKey) ? labels.placement.name : labels.name}
      value={{
        min: 0,
        max: frame.size.width,
        now: Math.round(shape.width),
        text: MESSAGES.processing.thisPage.pixels(shape.width),
      }}
      data={data}
      onKeyDown={onKeyDown}
    >
      <Rect
        x={topLeft.x}
        y={topLeft.y}
        width={bottomRight.x - topLeft.x}
        height={bottomRight.y - topLeft.y}
        stroke={FRAME_COLOR}
        strokeWidth={FRAME_WIDTH_PX}
        listening={false}
      />
      {places.map(({ handle, at }) => (
        <Rect
          key={handle}
          x={at.x}
          y={at.y}
          offsetX={HANDLE_SIDE_PX / HALF}
          offsetY={HANDLE_SIDE_PX / HALF}
          width={HANDLE_SIDE_PX}
          height={HANDLE_SIDE_PX}
          fill={FRAME_COLOR}
          stroke={HANDLE_BORDER_COLOR}
          strokeWidth={HANDLE_BORDER_PX}
          hitStrokeWidth={HIT_EXTRA_PX}
          draggable
          name={labels.handle(dashed(handle).replace('-', ' '))}
          onDragMove={drag(handle)}
          onDragEnd={() => onCommit(latest.current)}
        />
      ))}
    </EditorLayer>
  );
}
