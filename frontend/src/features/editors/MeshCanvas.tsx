import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef, useState } from 'react';
import { Circle, Line } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { FIGURE_STYLE } from '@/features/editors/figure';
import { nudgeOfKey } from '@/features/editors/line';
import { curvesOf, GRID_ROWS, gridOf, moveNode } from '@/features/editors/mesh';
import { useMoreControl } from '@/features/editors/moreControl';
import { useSceneFrame } from '@/features/editors/scene';
import type { MeshShape, Point } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { useDebouncedCommit } from '@/features/editors/useDebouncedCommit';
import { MESSAGES } from '@/shared/messages';

/**
 * The curves over the picture: the top curve and the bottom curve of the page, each a line through its nodes that the
 * reader lays on the first and the last line of text, or with "More control" the whole grid.
 *
 * A node is saved when it is let go. The arrow keys move the node that was grabbed last by a pixel, or by ten with Shift,
 * and the save waits for a pause in the keys so that a run of presses is saved once. A change made with only the two
 * curves shown keeps those two, and the server makes the page between them; a change made on the grid keeps the grid.
 */

const labels = MESSAGES.editors.mesh;

const CURVE_WIDTH_PX = 2;
/** How far a curve bends between its nodes, as Konva counts it. */
const CURVE_TENSION = 0.4;
const HANDLE_RADIUS_PX = 7;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
/** Quiet time after the last key before the nodes are saved. */
const KEY_SAVE_DELAY_MS = 600;

function pairOf(point: Point): string {
  return `${Math.round(point.x)},${Math.round(point.y)}`;
}

/** The node the reader grabbed last, which the arrow keys move. */
interface Grabbed {
  row: number;
  column: number;
}

export function MeshCanvas({
  scene,
  shape,
  size,
  figure,
  onChange,
  onCommit,
}: CanvasProps<MeshShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const { stroke, dash } = FIGURE_STYLE[figure];
  const saveLater = useDebouncedCommit(onCommit, KEY_SAVE_DELAY_MS);
  const showAll = useMoreControl();
  // What is on the screen: the grid, or the two curves of the shape
  const shown = showAll ? gridOf(shape, GRID_ROWS) : curvesOf(shape);
  // The drag handlers run between renders, so the shape they build on is the latest one and not the one they closed over
  const latest = useRef(shown);
  useEffect(() => {
    latest.current = shown;
  }, [shown]);
  const [grabbed, setGrabbed] = useState<Grabbed>({ row: 0, column: 0 });

  const { mapping } = frame;
  const screen = shown.rows.map((row) => row.map((node) => mapping.toScreen(node)));

  const drag = (row: number, column: number) => (event: KonvaEventObject<DragEvent>) => {
    setGrabbed({ row, column });
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = moveNode(latest.current, row, column, at, frame.size);
    latest.current = next;
    onChange(next);
    // The node follows the pointer only as far as the image goes
    const node = next.rows[row]?.[column];
    if (node !== undefined) {
      event.target.position(mapping.toScreen(node));
    }
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const step = nudgeOfKey(event.key, event.shiftKey);
    if (step === null || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    const from = latest.current.rows[grabbed.row]?.[grabbed.column];
    if (from === undefined) {
      return;
    }
    event.preventDefault();
    const next = moveNode(
      latest.current,
      grabbed.row,
      grabbed.column,
      { x: from.x + step.x, y: from.y + step.y },
      frame.size,
    );
    latest.current = next;
    onChange(next);
    saveLater(next);
  };

  // The curves and the handles are named by their place in the grid, which is all the identity they have
  const curves = screen.map((row, place) => ({
    id: `curve-${place}`,
    points: row.flatMap((node) => [node.x, node.y]),
  }));
  const handles = screen.flatMap((row, place) =>
    row.map((at, column) => ({ id: `node-${place}-${column}`, row: place, column, at })),
  );
  const data: Record<string, string> = {
    figure,
    rows: String(shown.rows.length),
    columns: String(shown.rows[0]?.length ?? 0),
  };
  for (const handle of handles) {
    const node = shown.rows[handle.row]?.[handle.column];
    data[`node-${handle.row}-${handle.column}`] = pairOf(node ?? handle.at);
    data[`handle-${handle.row}-${handle.column}`] = pairOf(handle.at);
  }

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: 0,
        max: GRID_ROWS,
        now: shown.rows.length,
        text: labels.valueText(shown.rows.length, shown.rows[0]?.length ?? 0),
      }}
      data={data}
      onKeyDown={onKeyDown}
    >
      {curves.map((curve) => (
        <Line
          key={curve.id}
          points={curve.points}
          stroke={stroke}
          dash={dash}
          strokeWidth={CURVE_WIDTH_PX}
          tension={CURVE_TENSION}
          listening={false}
        />
      ))}
      {handles.map((handle) => (
        <Circle
          key={handle.id}
          x={handle.at.x}
          y={handle.at.y}
          radius={HANDLE_RADIUS_PX}
          fill={stroke}
          stroke={HANDLE_BORDER_COLOR}
          strokeWidth={HANDLE_BORDER_PX}
          hitStrokeWidth={HIT_EXTRA_PX}
          draggable
          name={labels.node(handle.row + 1, handle.column + 1)}
          onDragMove={drag(handle.row, handle.column)}
          onDragEnd={() => onCommit(latest.current)}
        />
      ))}
    </EditorLayer>
  );
}
