import type { ContentType, PageSchema } from '@/api';
import { ConditionMark } from '@/features/workspace/steps';
import { MESSAGES } from '@/shared/messages';

/**
 * What a page shows, as the strip and the panel of a stage write it: the mark of the content on the thumbnail, and the
 * choice of pages a change of the content is made on.
 *
 * The server says what a page shows and where that comes from, so nothing here works the type out from the kind of the
 * page or the colour of its image.
 */

/** Give the mark a content type carries, the one the step bar gives the condition that processes the same pages. */
export function markOfContent(type: ContentType): ConditionMark {
  return type === 'text' ? ConditionMark.Text : ConditionMark.Picture;
}

/** Write what a page shows and where that comes from, as the title of the mark and its text for a reader of a screen. */
export function describeContent(page: Pick<PageSchema, 'content_type' | 'content_source'>): string {
  return MESSAGES.workspace.strip.content.mark(
    MESSAGES.pages.contentTypes[page.content_type],
    MESSAGES.pages.contentSources[page.content_source],
  );
}

/**
 * Choose the pages a change of the content is made on: the selected pages, or the open page when none is selected.
 *
 * @param items Every page of the book, in book order.
 * @param selected The identifiers of the pages selected in the grid.
 * @param currentId The open page, or undefined when none is open.
 */
export function pagesToChange(
  items: readonly { page: PageSchema }[],
  selected: ReadonlySet<string>,
  currentId: string | undefined,
): PageSchema[] {
  const chosen = items.filter(({ page }) => selected.has(page.id));
  const pages = chosen.length > 0 ? chosen : items.filter(({ page }) => page.id === currentId);
  return pages.map(({ page }) => page).filter((page) => page.origin !== 'placeholder');
}

/** How many of the pages have the content from the program, and how many from the reader. */
export function sourcesOf(pages: readonly PageSchema[]): { found: number; hand: number } {
  return {
    found: pages.filter((page) => page.content_source === 'detected').length,
    hand: pages.filter((page) => page.content_source === 'hand').length,
  };
}
