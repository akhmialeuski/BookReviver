import { EditorLayer } from '@/features/editors/EditorLayer';
import { layoutOf, RectFrame, rectData } from '@/features/editors/RectFrame';
import { nudgeRect } from '@/features/editors/rect';
import { useSceneFrame } from '@/features/editors/scene';
import { useShapeEditing, widthValue } from '@/features/editors/shapeEditing';
import type { RectShape } from '@/features/editors/shapes';
import type { CanvasProps } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The frame over the picture: the outline of the content of the page, with a handle on each corner and the middle of each
 * side to drag.
 *
 * A handle is saved when it is let go. The arrow keys move the whole frame by a pixel, or by ten with Shift, and the save
 * waits for a pause in the keys so that a run of presses is saved once.
 */

const labels = MESSAGES.editors.rect;

export function RectCanvas({
  scene,
  shape,
  size,
  figure,
  onChange,
  onCommit,
  onCommitLater,
}: CanvasProps<RectShape>): React.JSX.Element {
  const frame = useSceneFrame(scene, size);
  const editing = useShapeEditing(frame, shape, onChange, onCommit, onCommitLater);
  const layout = layoutOf(frame.mapping, shape);

  return (
    <EditorLayer
      scene={scene}
      frame={frame}
      label={labels.name}
      value={widthValue(frame, shape.width)}
      data={rectData(figure, shape, layout)}
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
      />
    </EditorLayer>
  );
}
