import { Picture } from '@/features/editors/types';

/**
 * The step that puts the block of text on a page of the book, whose frame editor works differently from the one of the
 * crop.
 *
 * Both steps offer the frame editor. The crop reads the picture before it and its frame says what to cut out of it, so the
 * editor lies on what the step reads. The normalize step reads the block and makes the page, and its frame says where on the
 * page the block goes and how large it is, so the editor lies on the page the step made and counts in that page's pixels.
 */

/** The key of the step that places the block of text on a page of the book. */
export const PLACEMENT_KEY = 'geometry.normalize';

/** Tell whether a step places the block of text on the page it makes. */
export function isPlacement(processorKey: string): boolean {
  return processorKey === PLACEMENT_KEY;
}

/**
 * Give the picture an editor lies on for a step, which is the page the step made when the step places a block and what the
 * editor asks for otherwise.
 *
 * @param picture The picture the editor of the step's kind asks for.
 * @param processorKey The key of the processor of the step.
 */
export function pictureFor(picture: Picture, processorKey: string): Picture {
  return picture === Picture.Input && isPlacement(processorKey) ? Picture.Output : picture;
}
