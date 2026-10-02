import type { PageSchema } from '@/api';
import { AnchorSide, type PageAnchor } from '@/features/pages/order';

/**
 * What a drag in the grid means for the order of the book.
 *
 * A grid with a sorted list's habits puts the dragged page where the page it is dropped on stands: after that page
 * when it stands later in the book than the dragged page, and before it when it stands earlier. Every selected page
 * travels with the one that is held, in the order they have in the book, and a drop on one of the travelling pages
 * names no place, since the place would be inside the group.
 */

/**
 * Tell which pages a drag carries: the selected pages when the held page is one of them, else the held page alone.
 *
 * @param selected The ids of the selected pages.
 * @param activeId The page the pointer or the keyboard holds.
 */
export function carriedBy(selected: ReadonlySet<string>, activeId: string): ReadonlySet<string> {
  return selected.has(activeId) ? selected : new Set([activeId]);
}

/**
 * Work out the place a drop names.
 *
 * @param pages The pages of the book in book order.
 * @param carried The pages that are dragged.
 * @param activeId The page that is held, which is one of the carried.
 * @param overId The page the drag is over.
 * @returns The place, or null when the drag is over a carried page or over no page of the book.
 */
export function dropPlace(
  pages: readonly PageSchema[],
  carried: ReadonlySet<string>,
  activeId: string,
  overId: string,
): PageAnchor | null {
  if (carried.has(overId)) {
    return null;
  }
  const from = pages.findIndex((page) => page.id === activeId);
  const to = pages.findIndex((page) => page.id === overId);
  if (from < 0 || to < 0) {
    return null;
  }
  return { pageId: overId, side: to > from ? AnchorSide.After : AnchorSide.Before };
}
