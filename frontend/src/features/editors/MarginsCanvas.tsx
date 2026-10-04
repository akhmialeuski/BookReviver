import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef, useState } from 'react';
import { Rect } from 'react-konva';
import {
  type Margins,
  marginAt,
  marginsOf,
  outerOf,
  settingOf,
  sidePoint,
  withMargin,
} from '@/features/editors/contentBox';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { FIGURE_STYLE } from '@/features/editors/figure';
import { nudgeOfKey } from '@/features/editors/line';
import {
  HANDLE_ORDER,
  handlePoint,
  moveHandle,
  nudgeRect,
  type RectHandle,
} from '@/features/editors/rect';
import { useSceneFrame } from '@/features/editors/scene';
import { type ContentBoxShape, dashed, type Point } from '@/features/editors/shapes';
import { useStepSettings } from '@/features/editors/stepSettings';
import type { CanvasProps } from '@/features/editors/types';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';
import { MARGIN_SIDES, type MarginSide } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * The content box and the border of its page over the picture the step reads.
 *
 * The inner rectangle is the box of the content, with a handle on each corner and the middle of each side; it is the edit of
 * the page and is saved when a handle is let go, or after a pause in the arrow keys. The outer rectangle is the box grown by
 * the margins of the page, with a handle on the middle of each side; a side let go sets the margin of that side as a setting
 * of this page alone. While the box moves, the border moves with it and keeps its margins.
 */

const labels = MESSAGES.editors.margins;

const FRAME_WIDTH_PX = 2;
const HANDLE_SIDE_PX = 12;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
/** Quiet time after the last key before the box is saved. */
const KEY_SAVE_DELAY_MS = 600;
const HALF = 2;

function pairOf(point: Point): string {
  return `${Math.round(point.x)},${Math.round(point.y)}`;
}

function listOf(rect: { left: number; top: number; width: number; height: number }): string {
  return [rect.left, rect.top, rect.width, rect.height].map(Math.round).join(',');
}

/** The margins a drag has set, which stand until the step has found the page again with the new setting. */
interface MovedMargins {
  found: string;
  margins: Margins;
}

export function MarginsCanvas({
  scene,
  shape,
  size,
  figure,
  context,
  onChange,
  onCommit,
}: CanvasProps<ContentBoxShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const settings = useStepSettings();
  const { stroke, dash } = FIGURE_STYLE[figure];
  const saveLater = useDebouncedCommit(onCommit, KEY_SAVE_DELAY_MS);
  // The drag handlers run between renders, so the shape they build on is the latest one and not the one they closed over
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);

  const { result } = context;
  const found = marginsOf(result);
  const foundKey = JSON.stringify(result?.marginBox ?? null);
  const [moved, setMoved] = useState<MovedMargins | null>(null);
  const margins = moved?.found === foundKey ? moved.margins : found;
  const latestMargins = useRef(margins);
  useEffect(() => {
    latestMargins.current = margins;
  }, [margins]);
  const names = result?.marginSettings ?? null;
  const blockScale = result?.blockScale ?? null;
  const sidesEditable = margins !== null && names !== null && blockScale !== null && blockScale > 0;

  const { mapping } = frame;
  const toScreen = (rect: { left: number; top: number; width: number; height: number }) => {
    const topLeft = mapping.toScreen({ x: rect.left, y: rect.top });
    const bottomRight = mapping.toScreen({ x: rect.left + rect.width, y: rect.top + rect.height });
    return {
      x: topLeft.x,
      y: topLeft.y,
      width: bottomRight.x - topLeft.x,
      height: bottomRight.y - topLeft.y,
    };
  };
  const inner = toScreen(shape);
  const outerRect = margins === null ? null : outerOf(shape, margins);
  const outer = outerRect === null ? null : toScreen(outerRect);
  const places = HANDLE_ORDER.map((handle) => ({
    handle,
    at: mapping.toScreen(handlePoint(shape, handle)),
  }));
  const sides =
    outerRect === null || !sidesEditable
      ? []
      : MARGIN_SIDES.map((side) => ({ side, at: mapping.toScreen(sidePoint(outerRect, side)) }));

  const drag = (handle: RectHandle) => (event: KonvaEventObject<DragEvent>) => {
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = moveHandle(latest.current, handle, at, frame.size);
    latest.current = next;
    onChange(next);
    // The handle follows the pointer only as far as the box may go
    event.target.position(mapping.toScreen(handlePoint(next, handle)));
  };

  const dragSide = (side: MarginSide) => (event: KonvaEventObject<DragEvent>) => {
    const current = latestMargins.current;
    if (current === null) {
      return;
    }
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = withMargin(current, side, marginAt(latest.current, side, at));
    latestMargins.current = next;
    setMoved({ found: foundKey, margins: next });
    // The handle stays on its side of the border, which only moves in and out
    event.target.position(mapping.toScreen(sidePoint(outerOf(latest.current, next), side)));
  };

  const letGoOfSide = (side: MarginSide) => () => {
    const current = latestMargins.current;
    if (current !== null && names !== null && blockScale !== null) {
      settings?.set(names[side], settingOf(current[side], blockScale));
    }
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

  const data: Record<string, string> = { figure, rect: listOf(shape) };
  if (outerRect !== null) {
    data.outer = listOf(outerRect);
  }
  for (const { handle, at } of places) {
    data[`handle-${dashed(handle)}`] = pairOf(at);
  }
  for (const { side, at } of sides) {
    data[`side-${side}`] = pairOf(at);
  }

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: 0,
        max: frame.size.width,
        now: Math.round(shape.width),
        text: MESSAGES.processing.thisPage.pixels(shape.width),
      }}
      data={data}
      onKeyDown={onKeyDown}
    >
      {outer === null ? null : (
        <Rect
          x={outer.x}
          y={outer.y}
          width={outer.width}
          height={outer.height}
          stroke={stroke}
          dash={dash}
          strokeWidth={FRAME_WIDTH_PX}
          listening={false}
        />
      )}
      <Rect
        x={inner.x}
        y={inner.y}
        width={inner.width}
        height={inner.height}
        stroke={stroke}
        dash={dash}
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
          fill={stroke}
          stroke={HANDLE_BORDER_COLOR}
          strokeWidth={HANDLE_BORDER_PX}
          hitStrokeWidth={HIT_EXTRA_PX}
          draggable
          name={labels.handle(dashed(handle).replace('-', ' '))}
          onDragMove={drag(handle)}
          onDragEnd={() => onCommit(latest.current)}
        />
      ))}
      {sides.map(({ side, at }) => (
        <Rect
          key={side}
          x={at.x}
          y={at.y}
          offsetX={HANDLE_SIDE_PX / HALF}
          offsetY={HANDLE_SIDE_PX / HALF}
          width={HANDLE_SIDE_PX}
          height={HANDLE_SIDE_PX}
          cornerRadius={HANDLE_SIDE_PX / HALF}
          fill={stroke}
          stroke={HANDLE_BORDER_COLOR}
          strokeWidth={HANDLE_BORDER_PX}
          hitStrokeWidth={HIT_EXTRA_PX}
          draggable
          name={labels.side(side)}
          onDragMove={dragSide(side)}
          onDragEnd={letGoOfSide(side)}
        />
      ))}
    </EditorLayer>
  );
}
