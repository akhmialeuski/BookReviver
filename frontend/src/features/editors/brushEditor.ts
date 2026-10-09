import { BrushCanvas } from '@/features/editors/BrushCanvas';
import { BrushPanel } from '@/features/editors/BrushPanel';
import { paintMask } from '@/features/editors/brush';
import { INPUT_EDITOR } from '@/features/editors/inputEditor';
import { type BrushShape, readBrush, writeBrush } from '@/features/editors/shapes';
import type { EditorDefinition } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * The brush of the eraser: the reader brushes over what the steps left on the page, and the area is painted out.
 *
 * It lies on the picture the eraser reads, which is the page after the steps before it in the stage. The edit is the
 * strokes and the mask painted from them, which the server keeps together: the mask is what the eraser reads, and the
 * strokes are what the editor draws again when the page is opened. The editor is offered once the eraser has run on the
 * page, since the step is what tells how large the picture is.
 */

export const brushEditor: EditorDefinition<BrushShape> = {
  ...INPUT_EDITOR,
  size: ({ result }) => sourceSize(result),
  fallback: () => ({ strokes: [] }),
  read: readBrush,
  write: writeBrush,
  describe: () => MESSAGES.processing.timeline.hand.mask,
  // The mask is what the eraser reads, so it was painted whether or not the strokes it came from were kept
  describeUnfit: MESSAGES.processing.timeline.hand.mask,
  mask: paintMask,
  Canvas: BrushCanvas,
  Panel: BrushPanel,
};
