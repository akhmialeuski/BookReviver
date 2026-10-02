import type { PageSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * What the panel of selected pages says about the group: the value the pages share, and the span of their numbers.
 */

/**
 * Give the value every page shares.
 *
 * @param values One value per selected page.
 * @returns The value when all are the same, or null when they differ or there are none.
 */
export function commonOf<T>(values: readonly T[]): T | null {
  const [first] = values;
  return first !== undefined && values.every((value) => value === first) ? first : null;
}

/**
 * Write the span of the selected pages as the header of the panel quotes it, such as `p. 44–46`.
 *
 * @param pages The selected pages in book order.
 * @returns The first and last page by printed number, or by place in the book for a page without a number.
 */
export function spanOf(pages: readonly PageSchema[]): string {
  const first = pages.at(0);
  const last = pages.at(-1);
  if (first === undefined || last === undefined) {
    return '';
  }
  const name = (page: PageSchema): string =>
    page.label === '' ? MESSAGES.order.tile.position(page.position + 1) : page.label;
  const text = MESSAGES.order.panel.range(name(first), name(last));
  return first.label === '' || last.label === '' ? text : MESSAGES.order.tile.printed(text);
}
