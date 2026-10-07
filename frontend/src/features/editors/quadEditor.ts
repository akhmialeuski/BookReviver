import { QuadCanvas } from '@/features/editors/QuadCanvas';
import { QuadPanel } from '@/features/editors/QuadPanel';
import { quadOf } from '@/features/editors/quad';
import { type QuadShape, readQuad, writeQuad } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * The sheet editor: the four corners of the paper, dragged on the picture the perspective step reads.
 *
 * Until the reader moves a corner the editor starts from the sheet the step found, or from the whole image when it found
 * none. The corners are in the pixels of the full image the step read, which the step reports once it has run and the
 * picture says before, so they stay put whatever size the picture on the screen is.
 */

export const quadEditor: EditorDefinition<QuadShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result, pictureSize }) => sourceSize(result) ?? pictureSize,
  runsAfterEdit: () => true,
  fallback: ({ size, result }) => quadOf(result, size),
  read: readQuad,
  write: writeQuad,
  describe: () => MESSAGES.processing.steps.pageHistory.hand.quad,
  Canvas: QuadCanvas,
  Panel: QuadPanel,
};
