import type { PageSchema } from '@/api';

/**
 * Choosing pages in the strip: single clicks, a range with the shift key, and dropping pages that were deleted.
 */

/**
 * The ids of the pages from one page to another in book order, both ends included.
 *
 * @param pages The pages of the book in book order.
 * @param fromId The page the range starts at, in either direction.
 * @param toId The page the range ends at.
 * @returns The ids between them, or just the target when the start is not in the book.
 */
export function selectRange(
  pages: readonly PageSchema[],
  fromId: string | null,
  toId: string,
): string[] {
  const to = pages.findIndex((page) => page.id === toId);
  const from = fromId === null ? to : pages.findIndex((page) => page.id === fromId);
  if (to < 0) {
    return [];
  }
  const [low, high] = from < 0 ? [to, to] : [Math.min(from, to), Math.max(from, to)];
  return pages.slice(low, high + 1).map((page) => page.id);
}

/**
 * Keep only the selected pages that are still in the book.
 *
 * @returns The same set when nothing was dropped, so a state update with it does not render again.
 */
export function pruneSelection(
  selected: ReadonlySet<string>,
  pages: readonly PageSchema[],
): ReadonlySet<string> {
  const present = new Set(pages.map((page) => page.id));
  const kept = [...selected].filter((id) => present.has(id));
  return kept.length === selected.size ? selected : new Set(kept);
}
