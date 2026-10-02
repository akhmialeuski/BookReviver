import type { PageKind, PageSchema } from '@/api';

/**
 * Which pages of a book the cover picker offers.
 *
 * A book of a hundred pages has a handful that can be its cover, so the picker shows the pages that look like one
 * by their kind, the page the library shows now and the first page, and offers every page on request. A book with
 * no chosen cover shows its first page in the library, so that page counts as the chosen one.
 */

/** The kinds of page that usually carry the picture of a book. */
const COVER_KINDS: ReadonlySet<PageKind> = new Set<PageKind>([
  'cover',
  'frontispiece',
  'title',
  'plate',
]);

/** A page the picker offers with its place in the book, counted from one. */
export interface CoverCandidate {
  readonly page: PageSchema;
  readonly number: number;
}

/**
 * Give the page the library shows as the cover of a book.
 *
 * @param pages The pages of the book in book order.
 * @param coverPageId The chosen cover, or null when none is chosen.
 * @returns The identifier of the chosen page, else of the first page, else null for a book with no pages.
 */
export function coverIdOf(pages: readonly PageSchema[], coverPageId: string | null): string | null {
  return coverPageId ?? pages[0]?.id ?? null;
}

/**
 * Give the pages the picker offers.
 *
 * @param pages The pages of the book in book order.
 * @param coverPageId The chosen cover, or null when none is chosen.
 * @param showAll Whether to offer every page.
 * @returns The pages in book order: every one, or those that look like a cover, the cover itself and the first.
 */
export function coverCandidates(
  pages: readonly PageSchema[],
  coverPageId: string | null,
  showAll: boolean,
): CoverCandidate[] {
  const chosen = coverIdOf(pages, coverPageId);
  return pages
    .map((page, index) => ({ page, number: index + 1 }))
    .filter(
      ({ page, number }) =>
        showAll || number === 1 || page.id === chosen || COVER_KINDS.has(page.kind),
    );
}
