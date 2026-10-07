import { MeshCanvas } from '@/features/editors/MeshCanvas';
import { MeshPanel } from '@/features/editors/MeshPanel';
import { meshOf } from '@/features/editors/mesh';
import { type MeshShape, readMesh, writeMesh } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * The curves editor: the top curve and the bottom curve of the page, dragged on the picture the dewarping step reads.
 *
 * Until the reader moves a node the editor starts from the curves the step found along the first and the last line of
 * text, or from two straight ones when it found none. The nodes are in the pixels of the full image the step read, which
 * the step reports, so they stay put whatever size the picture on the screen is. "More control" shows the whole grid.
 */

export const meshEditor: EditorDefinition<MeshShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result, pictureSize }) => sourceSize(result) ?? pictureSize,
  runsAfterEdit: () => true,
  fallback: ({ size, result }) => meshOf(result, size),
  read: readMesh,
  write: writeMesh,
  describe: () => MESSAGES.processing.timeline.hand.mesh,
  Canvas: MeshCanvas,
  Panel: MeshPanel,
};
