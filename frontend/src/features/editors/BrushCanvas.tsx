import type { KonvaEventObject } from 'konva/lib/Node';
import { useEffect, useRef } from 'react';
import { Circle, Line, Rect } from 'react-konva';
import { extendStroke, radiusOf, startStroke, useBrushPercent } from '@/features/editors/brush';
import { EditorLayer } from '@/features/editors/EditorLayer';
import { clampToScan } from '@/features/editors/line';
import { useSceneFrame } from '@/features/editors/scene';
import type { BrushShape, Point } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The brush over the page: a drag paints a stroke in translucent red, and the stroke is saved when the pointer is let go.
 *
 * The brush covers the whole page, so a drag on it paints and never pans, and the wheel still zooms. The strokes of the
 * page are drawn again from what was saved, so the brush goes on from where it was left.
 */

const labels = MESSAGES.editors.brush;

const STROKE_COLOR = 'rgba(220, 38, 38, 0.45)';
const HIT_COLOR = 'transparent';
const DIAMETER = 2;

export function BrushCanvas({
  scene,
  shape,
  size,
  onChange,
  onCommit,
}: CanvasProps<BrushShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const [percent] = useBrushPercent();
  // The pointer handlers run between renders, so the strokes they build on are the latest ones and not the ones they
  // closed over
  const latest = useRef(shape);
  useEffect(() => {
    latest.current = shape;
  }, [shape]);
  const painting = useRef(false);

  const { mapping } = frame;
  const pixel = mapping.pixelLength();
  const flat = (points: readonly Point[]): number[] =>
    points.flatMap((point) => {
      const at = mapping.toScreen(point);
      return [at.x, at.y];
    });
  const pointerAt = (event: KonvaEventObject<PointerEvent>): Point | null => {
    const place = event.target.getStage()?.getPointerPosition();
    return place === null || place === undefined
      ? null
      : clampToScan(mapping.toImage(place), frame.size);
  };

  const begin = (event: KonvaEventObject<PointerEvent>): void => {
    const at = pointerAt(event);
    if (at === null) {
      return;
    }
    painting.current = true;
    const next: BrushShape = {
      strokes: [...latest.current.strokes, startStroke(at, radiusOf(percent, frame.size))],
    };
    latest.current = next;
    onChange(next);
  };
  const move = (event: KonvaEventObject<PointerEvent>): void => {
    const at = pointerAt(event);
    if (!painting.current || at === null) {
      return;
    }
    const next = extendStroke(latest.current, at);
    latest.current = next;
    onChange(next);
  };
  const end = (): void => {
    if (painting.current) {
      painting.current = false;
      onCommit(latest.current);
    }
  };

  const entries = shape.strokes.map((stroke, place) => ({ id: `stroke-${place}`, stroke }));
  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={{
        min: 0,
        max: shape.strokes.length,
        now: shape.strokes.length,
        text: labels.strokes(shape.strokes.length),
      }}
      data={{ strokes: String(shape.strokes.length), brush: String(percent) }}
    >
      <Rect
        x={0}
        y={0}
        width={frame.stage.width}
        height={frame.stage.height}
        fill={HIT_COLOR}
        onPointerDown={begin}
        onPointerMove={move}
        onPointerUp={end}
        onPointerLeave={end}
      />
      {entries.map(({ id, stroke }) => {
        const [first] = stroke.points;
        if (first === undefined) {
          return null;
        }
        return stroke.points.length === 1 ? (
          <Circle
            key={id}
            x={mapping.toScreen(first).x}
            y={mapping.toScreen(first).y}
            radius={stroke.radius * pixel}
            fill={STROKE_COLOR}
            listening={false}
          />
        ) : (
          <Line
            key={id}
            points={flat(stroke.points)}
            stroke={STROKE_COLOR}
            strokeWidth={stroke.radius * DIAMETER * pixel}
            lineCap="round"
            lineJoin="round"
            listening={false}
          />
        );
      })}
    </EditorLayer>
  );
}
