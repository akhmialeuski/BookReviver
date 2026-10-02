import type { NewPageOrigin, PageCreate } from '@/api';
import { formatNumber, type LabelGap } from '@/features/pages/gaps';

/**
 * What adds pages in the Order stage, written as the bodies of the requests that add them: the entries of the Insert
 * menu, which put a blank leaf or a missing page before or after the selected pages or at the end of the book, and the
 * "Add missing" button of a gap, which puts a missing page for each number the gap lacks.
 */

/** Where the new page goes: before the first selected page, after the last one, or at the end of the book. */
export type InsertPlace = 'before' | 'after' | 'end';

/** One entry of the Insert menu. */
export interface InsertSpec {
  /** A blank leaf is a white page, and a missing page is a placeholder that waits for a scan. */
  origin: NewPageOrigin;
  place: InsertPlace;
}

/**
 * Write the request for an entry of the menu.
 *
 * @param spec The entry the reader chose.
 * @param selectedIds The ids of the selected pages in book order, which "before" and "after" are measured from.
 * @returns A blank leaf has the kind blank and a missing page the kind text. A place that needs a selected page
 * falls back to the end of the book when none is selected.
 */
export function insertBody(spec: InsertSpec, selectedIds: readonly string[]): PageCreate {
  const kind = spec.origin === 'blank' ? 'blank' : 'text';
  const first = selectedIds.at(0);
  const last = selectedIds.at(-1);
  if (spec.place === 'before' && first !== undefined) {
    return { origin: spec.origin, kind, before_page_id: first };
  }
  if (spec.place === 'after' && last !== undefined) {
    return { origin: spec.origin, kind, after_page_id: last };
  }
  return { origin: spec.origin, kind };
}

/**
 * Write the requests that add a missing page for each number a gap lacks.
 *
 * @param gap The jump in the printed numbers.
 * @returns One request per missing number, in number order. Each puts its page before the page the numbers lead to
 * and carries the number as its label, so sent one after the other they stand in the order of the numbers.
 */
export function missingPageBodies(gap: LabelGap): PageCreate[] {
  const bodies: PageCreate[] = [];
  for (let number = gap.firstMissing; number <= gap.lastMissing; number += 1) {
    bodies.push({
      origin: 'placeholder',
      kind: 'text',
      label: formatNumber(gap.style, number),
      before_page_id: gap.beforePageId,
    });
  }
  return bodies;
}
