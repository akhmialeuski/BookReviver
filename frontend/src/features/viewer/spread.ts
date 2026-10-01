/**
 * Which pages of the book one view of the viewer shows, and which views lie before and after it.
 *
 * A single view is one page. A spread follows the printed book: the first page lies alone, as the cover does on the
 * right-hand side, and every later pair is an odd page with the even page after it, so turning forward from page 1
 * shows pages 2 and 3, then 4 and 5. Positions here are indexes into the list of pages the viewer shows, from zero.
 */

/** The first index of the view that holds the page at `index`. */
export function viewStart(index: number, spread: boolean): number {
  if (!spread || index <= 0) {
    return Math.max(index, 0);
  }
  return index % 2 === 1 ? index : index - 1;
}

/**
 * The indexes of the pages of the view that holds the page at `index`.
 *
 * @param index Index of a page, clamped into the book.
 * @param count Number of pages in the book.
 * @param spread Whether two pages are shown at once.
 * @returns One index, or two for a spread whose second page exists; empty for a book without pages.
 */
export function viewIndexes(index: number, count: number, spread: boolean): number[] {
  if (count <= 0) {
    return [];
  }
  const start = viewStart(Math.min(index, count - 1), spread);
  if (spread && start > 0 && start + 1 < count) {
    return [start, start + 1];
  }
  return [start];
}

/**
 * The first index of the view before the one holding `index`, or null when that view is the first.
 *
 * A spread turns back to the pair before it, and from the first pair to the page that lies alone.
 */
export function previousViewStart(index: number, count: number, spread: boolean): number | null {
  const [first] = viewIndexes(index, count, spread);
  if (first === undefined || first === 0) {
    return null;
  }
  if (!spread) {
    return first - 1;
  }
  return first <= 1 ? 0 : first - 2;
}

/** The first index of the view after the one holding `index`, or null when that view is the last. */
export function nextViewStart(index: number, count: number, spread: boolean): number | null {
  const indexes = viewIndexes(index, count, spread);
  const last = indexes.at(-1);
  if (last === undefined || last + 1 >= count) {
    return null;
  }
  return last + 1;
}

/** The first index of the last view, which `End` jumps to. */
export function lastViewStart(count: number, spread: boolean): number | null {
  return count <= 0 ? null : viewStart(count - 1, spread);
}
