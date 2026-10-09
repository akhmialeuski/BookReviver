import { INPUT_EDITOR } from '@/features/editors/inputEditor';
import { QuadCanvas } from '@/features/editors/QuadCanvas';
import { quadOf } from '@/features/editors/quad';
import { type QuadShape, readQuad, writeQuad } from '@/features/editors/shapes';
import type { EditorDefinition } from '@/features/editors/types';
import { MESSAGES } from '@/shared/messages';

/**
 * The sheet editor: the four corners of the paper, dragged on the picture the perspective step reads.
 *
 * Until the reader moves a corner the editor starts from the sheet the step found, or from the whole image when it found
 * none. The corners are in the pixels of the full image the step read, which the step reports once it has run and the
 * picture says before, so they stay put whatever size the picture on the screen is.
 */

export const quadEditor: EditorDefinition<QuadShape> = {
  ...INPUT_EDITOR,
  fallback: ({ size, result }) => quadOf(result, size),
  read: readQuad,
  write: writeQuad,
  describe: () => MESSAGES.processing.timeline.hand.quad,
  Canvas: QuadCanvas,
};
