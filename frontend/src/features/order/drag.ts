import { type CollisionDetection, closestCenter } from '@dnd-kit/core';
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

/**
 * Find the page a drop lands on by what is under the pointer on the screen right now.
 *
 * dnd-kit compares the held page with the rectangles it measured earlier, and on a long book that scrolls and is
 * virtualized those rectangles are stale, so a drop lands a row off. The document knows where the tiles are at this
 * moment: the topmost element under the pointer that sits in a cell (`data-cell`) names the page, and the droppable
 * with that id is the collision. Without a pointer, as with the keyboard, or with no registered cell under it, such as
 * a gap card or the space between tiles, the answer is that of dnd-kit's `closestCenter`.
 *
 * @param args What dnd-kit hands to every collision detection: the pointer and the droppables.
 * @returns The collisions, the one under the pointer alone when there is one.
 */
export const collideUnderPointer: CollisionDetection = (args) => {
  const { pointerCoordinates, droppableContainers } = args;
  if (pointerCoordinates !== null) {
    for (const element of document.elementsFromPoint(pointerCoordinates.x, pointerCoordinates.y)) {
      const cellId = element.closest<HTMLElement>('[data-cell]')?.dataset.cell;
      const droppable = droppableContainers.find((container) => String(container.id) === cellId);
      if (droppable !== undefined) {
        return [{ id: droppable.id }];
      }
    }
  }
  return closestCenter(args);
};
