import { Circle, Line } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { HANDLE_STYLE } from '@/features/editors/handles';
import { crossingX, LineEnd, moveEnd, nudgeLine } from '@/features/editors/line';
import { useSceneFrame } from '@/features/editors/scene';
import { useShapeEditing, widthValue } from '@/features/editors/shapeEditing';
import { type LineShape, type Point, pairOf } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
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
/** Room the labels keep from the edge of the canvas and from the scan. */
const LABEL_EDGE_PX = 8;
const LABEL_GAP_PX = 12;
const LABEL_HEIGHT_PX = 28;
const HALF = 2;

export function LineCanvas({
  scene,
  shape,
  size,
  context,
  onChange,
  onCommit,
}: CanvasProps<LineShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const editing = useShapeEditing(frame, shape, onChange, onCommit);

  const { mapping } = frame;
  const start = mapping.toScreen(shape.start);
  const end = mapping.toScreen(shape.end);

  const onKeyDown = editing.onKeyDown((current, step) =>
    nudgeLine(current, step.x, step.y, frame.size),
  );

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
      {...HANDLE_STYLE}
      radius={HANDLE_RADIUS_PX}
      fill={LINE_COLOR}
      name={name}
      onDragMove={editing.drag(
        (current, to) => moveEnd(current, which, to, frame.size),
        (next) => (which === LineEnd.Start ? next.start : next.end),
      )}
      onDragEnd={editing.release}
    />
  );

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={widthValue(frame, cut)}
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
