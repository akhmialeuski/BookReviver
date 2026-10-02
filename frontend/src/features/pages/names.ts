import type { PageSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * The short name of a page in a sentence, a chip or a button: its printed number as `p. 44`, or its place in the book
 * as `#5` when it has none.
 */

export function shortName(page: Pick<PageSchema, 'label' | 'position'>): string {
  return page.label === ''
    ? MESSAGES.order.tile.position(page.position + 1)
    : MESSAGES.order.tile.printed(page.label);
}
