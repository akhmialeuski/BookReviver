import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef } from 'react';
import { Circle, Line } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { crossingX, LineEnd, moveEnd, nudgeLine, nudgeOfKey } from '@/features/editors/line';
import { useSceneFrame } from '@/features/editors/scene';
import type { LineShape, Point } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';
import { isSplit, pagesOfScan } from '@/features/processing/split';
import { MESSAGES } from '@/shared/messages';

/**
 * The split line over the scan: a dashed line with an end to drag at the top and one at the bottom, and over the scan the
 * labels that say which pages the two halves become.
 *
 * The line is saved when an end is let go. The arrow keys move the whole line by a pixel, or by ten with Shift, and the
 * save waits for a pause in the keys so that a run of presses is saved once.
 */

const labels = MESSAGES.editors.line;

const LINE_COLOR = '#0ea5e9';
const LINE_WIDTH_PX = 2;
const LINE_DASH_PX = [8, 6];
const HANDLE_RADIUS_PX = 8;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
/** Quiet time after the last key before the line is saved. */
const KEY_SAVE_DELAY_MS = 600;
/** Room the labels keep from the edge of the canvas and from the scan. */
const LABEL_EDGE_PX = 8;
const LABEL_GAP_PX = 12;
const LABEL_HEIGHT_PX = 28;
const HALF = 2;

function pairOf(point: Point): string {
  return `${Math.round(point.x)},${Math.round(point.y)}`;
}

export function LineCanvas({
  scene,
  shape,
  size,
  context,
  onChange,
  onCommit,
}: CanvasProps<LineShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const saveLater = useDebouncedCommit(onCommit, KEY_SAVE_DELAY_MS);
  // The drag handlers run between renders, so the shape they build on is the latest one and not the one they closed over
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);

  const { mapping } = frame;
  const start = mapping.toScreen(shape.start);
  const end = mapping.toScreen(shape.end);

  const drag = (which: LineEnd) => (event: KonvaEventObject<DragEvent>) => {
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = moveEnd(latest.current, which, at, frame.size);
    latest.current = next;
    onChange(next);
    // The end follows the pointer only as far as the line may go
    event.target.position(mapping.toScreen(which === LineEnd.Start ? next.start : next.end));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const step = nudgeOfKey(event.key, event.shiftKey);
    if (step === null || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    event.preventDefault();
    const next = nudgeLine(latest.current, step.x, step.y, frame.size);
    latest.current = next;
    onChange(next);
    saveLater(next);
  };

  const scanId = context.current.page.scan_id;
  const scanPages =
    scanId === null
      ? []
      : pagesOfScan(
          context.items.map((item) => item.page),
          scanId,
        );
  const split = isSplit(scanPages);
  const nameOf = (slot: number): string | null => {
    const half = scanPages.find((candidate) => candidate.slot === slot);
    return split && half !== undefined ? labels.pageName(half.label, half.position) : null;
  };
  const cut = crossingX(shape, frame.size.height);
  const top = mapping.toScreen({ x: 0, y: 0 }).y;
  const labelTop = Math.max(LABEL_EDGE_PX, top - LABEL_HEIGHT_PX - LABEL_GAP_PX);
  const pills = [
    { key: 'left', text: labels.left(nameOf(1)), centre: cut / HALF },
    { key: 'right', text: labels.right(nameOf(2)), centre: (cut + frame.size.width) / HALF },
  ];

  const handle = (which: LineEnd, at: Point, name: string): React.JSX.Element => (
    <Circle
      x={at.x}
      y={at.y}
      radius={HANDLE_RADIUS_PX}
      fill={LINE_COLOR}
      stroke={HANDLE_BORDER_COLOR}
      strokeWidth={HANDLE_BORDER_PX}
      hitStrokeWidth={HIT_EXTRA_PX}
      draggable
      name={name}
      onDragMove={drag(which)}
      onDragEnd={() => onCommit(latest.current)}
    />
  );

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: 0,
        max: frame.size.width,
        now: Math.round(cut),
        text: MESSAGES.processing.thisPage.pixels(cut),
      }}
      data={{
        'line-start': pairOf(shape.start),
        'line-end': pairOf(shape.end),
        'handle-start': pairOf(start),
        'handle-end': pairOf(end),
        'scan-top': String(Math.round(top)),
      }}
      onKeyDown={onKeyDown}
      overlay={pills.map((pill) => (
        <p
          key={pill.key}
          data-testid={`line-label-${pill.key}`}
          className="absolute -translate-x-1/2 rounded-full bg-sky-600 px-3 py-1 text-xs whitespace-nowrap text-white shadow"
          style={{ left: mapping.toScreen({ x: pill.centre, y: 0 }).x, top: labelTop }}
        >
          {pill.text}
        </p>
      ))}
    >
      <Line
        points={[start.x, start.y, end.x, end.y]}
        stroke={LINE_COLOR}
        strokeWidth={LINE_WIDTH_PX}
        dash={LINE_DASH_PX}
        listening={false}
      />
      {handle(LineEnd.Start, start, labels.start)}
      {handle(LineEnd.End, end, labels.end)}
    </EditorLayer>
  );
}
