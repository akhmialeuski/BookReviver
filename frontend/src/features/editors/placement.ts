/**
 * The step that puts the content box of a page on a page of the book, which has its own settings and its own measure of
 * the book in the panel, apart from its editor.
 */

/** The key of the step that places the content box on a page of the book. */
export const PLACEMENT_KEY = 'geometry.normalize';

/** Tell whether a step places the content box on the page it makes. */
export function isPlacement(processorKey: string): boolean {
  return processorKey === PLACEMENT_KEY;
}
