import type { ContentType, PageSchema } from '@/api';
import { MESSAGES } from '@/shared/messages';

/**
 * What a page shows, as the strip and the toolbar of a stage write it: the mark of the content on the thumbnail, and the
 * choice of pages a change of the content is made on.
 *
 * The server says what a page shows and where that comes from, so nothing here works the type out from the kind of the
 * page or the colour of its image.
 */

/** The marks a content type carries on the thumbnail of a page. */
export const ContentMark = {
  Text: 'text',
  Picture: 'picture',
} as const;

/** One mark (derived from {@link ContentMark}). */
export type ContentMark = (typeof ContentMark)[keyof typeof ContentMark];

/** Give the mark a content type carries. */
export function markOfContent(type: ContentType): ContentMark {
  return type === 'text' ? ContentMark.Text : ContentMark.Picture;
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
