import { BrushCanvas } from '@/features/editors/BrushCanvas';
import { BrushPanel } from '@/features/editors/BrushPanel';
import { paintMask } from '@/features/editors/brush';
import { type BrushShape, readBrush, writeBrush } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';

/**
 * The brush of the eraser: the reader brushes over what the steps left on the page, and the area is painted out.
 *
 * It lies on the picture the eraser reads, which is the page after the steps before it in the stage. The edit is the
 * strokes and the mask painted from them, which the server keeps together: the mask is what the eraser reads, and the
 * strokes are what the editor draws again when the page is opened. The editor is offered once the eraser has run on the
 * page, since the step is what tells how large the picture is.
 */

export const brushEditor: EditorDefinition<BrushShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result }) => sourceSize(result),
  runsAfterEdit: () => true,
  fallback: () => ({ strokes: [] }),
  read: readBrush,
  write: writeBrush,
  mask: paintMask,
  Canvas: BrushCanvas,
  Panel: BrushPanel,
};
