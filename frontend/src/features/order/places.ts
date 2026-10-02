import type { PageSchema } from '@/api';
import { gapKey } from '@/features/order/layout';
import type { LabelGap } from '@/features/pages/gaps';

/**
 * The places of the book that ask for a look in the Order stage: the gaps in the printed numbers and the pages that
 * stand for a scan the book does not have yet.
 */

/** One place to check, with the key of the cell the grid draws it in. */
export interface PlaceToCheck {
  kind: 'gap' | 'missing';
  /** The key of a gap card, or the id of a missing page, which is also the key of its tile. */
  cellId: string;
}

/**
 * List the places to check in book order.
 *
 * @param pages The pages of the book in book order.
 * @param gaps The gaps in the printed numbers.
 * @returns A place for every gap and for every page of the book that waits for a scan; a page left out of the book
 * is not part of it and asks for nothing.
 */
export function placesToCheck(
  pages: readonly PageSchema[],
  gaps: readonly LabelGap[],
): PlaceToCheck[] {
  const indexOf = new Map(pages.map((page, index) => [page.id, index]));
  const places: { at: number; place: PlaceToCheck }[] = [];
  for (const gap of gaps) {
    // A gap card stands just before the page the numbers lead to
    places.push({
      at: (indexOf.get(gap.beforePageId) ?? 0) - 0.5,
      place: { kind: 'gap', cellId: gapKey(gap) },
    });
  }
  pages.forEach((page, index) => {
    if (page.included && page.origin === 'placeholder') {
      places.push({ at: index, place: { kind: 'missing', cellId: page.id } });
    }
  });
  return places.sort((left, right) => left.at - right.at).map((entry) => entry.place);
}
