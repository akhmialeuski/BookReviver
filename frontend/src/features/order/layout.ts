import type { PageSchema } from '@/api';
import type { LabelGap } from '@/features/pages/gaps';
import { nextViewStart, viewIndexes } from '@/features/viewer/spread';

/**
 * What the grid of the Order stage draws, in order: the pages, alone or in the spreads the viewer shows, and the card
 * of a gap in the printed numbers wherever the missing pages would stand.
 *
 * A spread follows the rule of the viewer (`features/viewer/spread.ts`), so the cover lies alone on the right and the
 * pairs after it are an odd page with the even page that follows. A page that lies alone has an empty slot on the side
 * where the book has no page, which is how a spread of the grid shows which side a page falls on.
 */

/** The side of a spread that has no page. */
export type EmptySide = 'left' | 'right';

/** A card or a group of pages of the grid. */
export type LayoutItem =
  | { kind: 'gap'; key: string; gap: LabelGap }
  | { kind: 'group'; key: string; pages: PageSchema[]; emptySide: EmptySide | null };

/** The id of the cell of a gap in the grid, by which the grid is scrolled to it. */
export function gapKey(gap: LabelGap): string {
  return `gap-${gap.afterPageId}-${gap.beforePageId}`;
}

/**
 * Lay the pages of the book out.
 *
 * @param pages The pages of the book in book order.
 * @param gaps The gaps in the printed numbers; each card stands before the page the missing numbers lead to.
 * @param spread Whether to group the pages in spreads, or to give each page its own group.
 * @returns The cards and groups in the order the grid draws them.
 */
export function layoutOf(
  pages: readonly PageSchema[],
  gaps: readonly LabelGap[],
  spread: boolean,
): LayoutItem[] {
  const items: LayoutItem[] = [];
  let start: number | null = pages.length === 0 ? null : 0;
  while (start !== null) {
    const indexes = viewIndexes(start, pages.length, spread);
    const group = indexes.flatMap((index) => pages[index] ?? []);
    for (const gap of gaps) {
      if (group.some((page) => page.id === gap.beforePageId)) {
        items.push({ kind: 'gap', key: gapKey(gap), gap });
      }
    }
    const lonely = spread && group.length === 1;
    items.push({
      kind: 'group',
      key: group[0]?.id ?? String(start),
      pages: group,
      emptySide: lonely ? (start === 0 ? 'left' : 'right') : null,
    });
    start = nextViewStart(start, pages.length, spread);
  }
  return items;
}
