import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef } from 'react';
import { Line, Rect } from 'react-konva';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { boundsOf, moveCorner } from '@/features/editors/regions';
import { useSceneFrame } from '@/features/editors/scene';
import { type Point, type RegionsShape, ZoneMode, type ZoneShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The picture zones over the page: the zones the step found outlined in blue, and the zones the reader drew filled in
 * green where they add a picture and in red where they remove one, each with a handle on every corner to drag.
 *
 * A corner is saved when it is let go, and the page is made again with the new zones.
 */

const labels = MESSAGES.editors.regions;

const FOUND_COLOR = '#2563eb';
const FOUND_DASH = [8, 6];
const ADD_COLOR = '#16a34a';
const REMOVE_COLOR = '#dc2626';
const FILL_ALPHA = '33';
const OUTLINE_WIDTH_PX = 2;
const HANDLE_SIDE_PX = 12;
const HANDLE_BORDER_PX = 2;
const HANDLE_BORDER_COLOR = '#ffffff';
const HIT_EXTRA_PX = 8;
const HALF = 2;

function colorOf(zone: ZoneShape): string {
  return zone.mode === ZoneMode.Add ? ADD_COLOR : REMOVE_COLOR;
}

export function RegionsCanvas({
  scene,
  shape,
  size,
  context,
  onChange,
  onCommit,
}: CanvasProps<RegionsShape>): React.JSX.Element | null {
  const frame = useSceneFrame(scene, size);
  // The drag handlers run between renders, so the shape they build on is the latest one and not the one they closed over
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);

  const { mapping } = frame;
  const flat = (points: readonly Point[]): number[] =>
    points.flatMap((point) => {
      const at = mapping.toScreen(point);
      return [at.x, at.y];
    });
  // The zones are named by their place in the list, which a drag never changes
  const entries = shape.zones.map((zone, place) => ({ id: `zone-${place}`, zone, place }));
  const foundEntries = (context.result?.zones ?? []).map((zone, place) => ({
    id: `found-${place}`,
    zone,
  }));

  const drag = (zone: number, corner: number) => (event: KonvaEventObject<DragEvent>) => {
    const at = mapping.toImage({ x: event.target.x(), y: event.target.y() });
    const next = moveCorner(latest.current, zone, corner, at, frame.size);
    latest.current = next;
    onChange(next);
    const moved = next.zones[zone]?.points[corner];
    if (moved !== undefined) {
      event.target.position(mapping.toScreen(moved));
    }
  };

  // A handle is named by the zone and the corner it moves, and its place on the screen is what the scenarios read
  const handles: { key: string; zone: ZoneShape; place: number; corner: number; at: Point }[] = [];
  const data: Record<string, string> = { zones: String(shape.zones.length) };
  for (const { id, zone, place } of entries) {
    data[id] = boundsOf(zone.points).map(Math.round).join(',');
    for (const [corner, point] of zone.points.entries()) {
      const at = mapping.toScreen(point);
      handles.push({ key: `${id}-handle-${corner}`, zone, place, corner, at });
      data[`handle-${place}-${corner}`] = `${Math.round(at.x)},${Math.round(at.y)}`;
    }
  }

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: 0,
        max: shape.zones.length,
        now: shape.zones.length,
        text: labels.zones,
      }}
      data={data}
    >
      {foundEntries.map(({ id, zone }) => (
        <Line
          key={id}
          points={flat(zone.points)}
          closed
          stroke={FOUND_COLOR}
          strokeWidth={OUTLINE_WIDTH_PX}
          dash={FOUND_DASH}
          listening={false}
        />
      ))}
      {entries.map(({ id, zone }) => (
        <Line
          key={id}
          points={flat(zone.points)}
          closed
          stroke={colorOf(zone)}
          fill={`${colorOf(zone)}${FILL_ALPHA}`}
          strokeWidth={OUTLINE_WIDTH_PX}
          listening={false}
        />
      ))}
      {handles.map(({ key, zone, place, corner, at }) => (
        <Rect
          key={key}
          x={at.x}
          y={at.y}
          offsetX={HANDLE_SIDE_PX / HALF}
          offsetY={HANDLE_SIDE_PX / HALF}
          width={HANDLE_SIDE_PX}
          height={HANDLE_SIDE_PX}
          fill={colorOf(zone)}
          stroke={HANDLE_BORDER_COLOR}
          strokeWidth={HANDLE_BORDER_PX}
          hitStrokeWidth={HIT_EXTRA_PX}
          draggable
          name={labels.handle(place + 1, corner + 1)}
          onDragMove={drag(place, corner)}
          onDragEnd={() => onCommit(latest.current)}
        />
      ))}
    </EditorLayer>
  );
}
