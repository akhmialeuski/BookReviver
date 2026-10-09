import { INPUT_EDITOR } from '@/features/editors/inputEditor';
import { MeshCanvas } from '@/features/editors/MeshCanvas';
import { MeshPanel } from '@/features/editors/MeshPanel';
import { meshOf } from '@/features/editors/mesh';
import { type MeshShape, readMesh, writeMesh } from '@/features/editors/shapes';
import type { EditorDefinition } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The curves editor: the top curve and the bottom curve of the page, dragged on the picture the dewarping step reads.
 *
 * Until the reader moves a node the editor starts from the curves the step found along the first and the last line of
 * text, or from two straight ones when it found none. The nodes are in the pixels of the full image the step read, which
 * the step reports, so they stay put whatever size the picture on the screen is. "More control" shows the whole grid.
 */

export const meshEditor: EditorDefinition<MeshShape> = {
  ...INPUT_EDITOR,
  fallback: ({ size, result }) => meshOf(result, size),
  read: readMesh,
  write: writeMesh,
  describe: () => MESSAGES.processing.timeline.hand.mesh,
  Canvas: MeshCanvas,
  Panel: MeshPanel,
};
