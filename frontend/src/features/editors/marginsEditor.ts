import { contentBoxOf } from '@/features/editors/contentBox';
import { MarginsCanvas } from '@/features/editors/MarginsCanvas';
import { MarginsPanel } from '@/features/editors/MarginsPanel';
import { type ContentBoxShape, readContentBox, writeContentBox } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';

/**
 * The editor of the Margins step: the box of the content of the page, which is the edit, and the border of the page grown
 * from it by the margins, whose sides set the margins of the page.
 *
 * It lies on the picture the step reads, in its pixels, and starts from the box the step found, which the server works out
 * for a page that has not been run too. The alignment of the box on the page is chosen in the panel. The border may lie
 * beyond the picture, so the canvas is fitted to hold the picture and the border both.
 */

export const marginsEditor: EditorDefinition<ContentBoxShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result, pictureSize }) => sourceSize(result) ?? pictureSize,
  runsAfterEdit: () => true,
  // The border of the page is wider than the picture when the margins are, so the canvas is fitted to hold it
  reach: ({ result }) => result?.marginBox ?? null,
  fallback: ({ size, result }) => contentBoxOf(result, size),
  read: readContentBox,
  write: writeContentBox,
  Canvas: MarginsCanvas,
  Panel: MarginsPanel,
};
