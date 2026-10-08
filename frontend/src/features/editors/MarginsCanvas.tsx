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
import { ROUND_SQUARE_HANDLE } from '@/features/editors/handles';
import { layoutOf, RectFrame, rectData, rectList, screenBox } from '@/features/editors/RectFrame';
import { nudgeRect } from '@/features/editors/rect';
import { useSceneFrame } from '@/features/editors/scene';
import { useShapeEditing, widthValue } from '@/features/editors/shapeEditing';
import { type ContentBoxShape, pairOf } from '@/features/editors/shapes';
import { useStepSettings } from '@/features/editors/stepSettings';
import type { CanvasProps } from '@/features/editors/types';
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
  const { stroke } = FIGURE_STYLE[figure];
  const editing = useShapeEditing(frame, shape, onChange, onCommit);
  const { latest } = editing;

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
  const pixelsPerMm = result?.marginPixelsPerMm ?? null;
  const sidesEditable =
    margins !== null && names !== null && pixelsPerMm !== null && pixelsPerMm > 0;

  const { mapping } = frame;
  const layout = layoutOf(mapping, shape);
  const outerRect = margins === null ? null : outerOf(shape, margins);
  const outer = outerRect === null ? null : screenBox(mapping, outerRect);
  const sides =
    outerRect === null || !sidesEditable
      ? []
      : MARGIN_SIDES.map((side) => ({ side, at: mapping.toScreen(sidePoint(outerRect, side)) }));

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
    if (current !== null && names !== null && pixelsPerMm !== null && pixelsPerMm > 0) {
      settings?.set(names[side], settingOf(current[side], pixelsPerMm));
    }
  };

  const data = rectData(figure, shape, layout);
  if (outerRect !== null) {
    data.outer = rectList(outerRect);
  }
  for (const { side, at } of sides) {
    data[`side-${side}`] = pairOf(at);
  }

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={widthValue(frame, shape.width)}
      data={data}
      onKeyDown={editing.onKeyDown((current, step) =>
        nudgeRect(current, step.x, step.y, frame.size),
      )}
    >
      <RectFrame
        frame={frame}
        layout={layout}
        figure={figure}
        editing={editing}
        handleName={labels.handle}
        outer={outer}
      />
      {sides.map(({ side, at }) => (
        <Rect
          key={side}
          {...ROUND_SQUARE_HANDLE}
          x={at.x}
          y={at.y}
          fill={stroke}
          name={labels.side(side)}
          onDragMove={dragSide(side)}
          onDragEnd={letGoOfSide(side)}
        />
      ))}
    </EditorLayer>
  );
}
