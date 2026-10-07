import { RectCanvas } from '@/features/editors/RectCanvas';
import { RectPanel } from '@/features/editors/RectPanel';
import { rectOf } from '@/features/editors/rect';
import { type RectShape, readRect, writeRect } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * The frame editor: the frame of the content of the page, with a handle on each corner and each side.
 *
 * For the crop step it lies on the picture the step reads, which is the page after the steps before it, and not on the scan.
 * Until the reader moves a handle the editor starts from the frame the step found, or from the page less a free margin when
 * it found none. The margin the step adds round the frame is not part of it.
 */

export const rectEditor: EditorDefinition<RectShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result, pictureSize }) => sourceSize(result) ?? pictureSize,
  runsAfterEdit: () => true,
  fallback: ({ size, result }) => rectOf(result, size),
  read: readRect,
  write: writeRect,
  describe: ({ left, top, width, height }) =>
    MESSAGES.processing.timeline.hand.frame(
      Math.round(left),
      Math.round(top),
      Math.round(width),
      Math.round(height),
    ),
  Canvas: RectCanvas,
  Panel: RectPanel,
};
