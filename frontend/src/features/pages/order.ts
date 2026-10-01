import type { PageSchema } from '@/api';

/**
 * The order of the pages of a book, worked out in the browser the way the server works it out.
 *
 * A move is sent to the server and takes effect there, but the strip should not wait for the answer to show it, so
 * the same rule is applied to the cached manifest at once and the manifest is read again afterwards to settle on the
 * server's order. The rule is the one of the page order stage: the pages that move keep the order they had in the
 * book, and they stand together before or after the anchor, which may not be one of them.
 */

/** Which side of the anchor page the moved pages are put on. */
export const AnchorSide = {
  Before: 'before',
  After: 'after',
} as const;

/** One side of an anchor (derived from {@link AnchorSide}). */
export type AnchorSide = (typeof AnchorSide)[keyof typeof AnchorSide];

/** A place in the book: before or after a page. */
export interface PageAnchor {
  pageId: string;
  side: AnchorSide;
}

/** The two fields of a request body that name the place, of which exactly one is set. */
export interface AnchorBody {
  before_page_id?: string;
  after_page_id?: string;
}

/** Write an anchor as the fields of a move request. */
export function anchorBody(anchor: PageAnchor): AnchorBody {
  return anchor.side === AnchorSide.Before
    ? { before_page_id: anchor.pageId }
    : { after_page_id: anchor.pageId };
}

/** Read the anchor a move request names, or null when it names none or both. */
export function anchorOf(body: {
  before_page_id?: string | null;
  after_page_id?: string | null;
}): PageAnchor | null {
  const { before_page_id: before, after_page_id: after } = body;
  if (typeof before === 'string' && after == null) {
    return { pageId: before, side: AnchorSide.Before };
  }
  if (typeof after === 'string' && before == null) {
    return { pageId: after, side: AnchorSide.After };
  }
  return null;
}

/** Number the pages by their place in the list, which is the position the server computes over a whole manifest. */
function withPositions(pages: readonly PageSchema[]): PageSchema[] {
  return pages.map((page, index) =>
    page.position === index ? page : { ...page, position: index },
  );
}

/**
 * Put pages before or after an anchor page.
 *
 * @param pages The pages of the book in book order.
 * @param ids The pages to move; they keep the order they have in the book, whatever the order of this list.
 * @param anchor The place to move them to.
 * @returns The pages in the new order with their positions renumbered, or null when nothing can move: no page to
 * move is in the book, the anchor is not in the book, or the anchor is one of the moved pages, which the server
 * refuses as a conflict.
 */
export function movePages(
  pages: readonly PageSchema[],
  ids: Iterable<string>,
  anchor: PageAnchor,
): PageSchema[] | null {
  const moving = new Set(ids);
  const moved = pages.filter((page) => moving.has(page.id));
  if (moved.length === 0 || moving.has(anchor.pageId)) {
    return null;
  }
  const rest = pages.filter((page) => !moving.has(page.id));
  const at = rest.findIndex((page) => page.id === anchor.pageId);
  if (at < 0) {
    return null;
  }
  const insertAt = anchor.side === AnchorSide.Before ? at : at + 1;
  return withPositions([...rest.slice(0, insertAt), ...moved, ...rest.slice(insertAt)]);
}

/** The ids of the pages cut from a source, in book order. */
export function pageIdsOfSource(pages: readonly PageSchema[], sourceId: string): string[] {
  return pages.filter((page) => page.source_id === sourceId).map((page) => page.id);
}

/** The first page of the book that was cut from a source, if any page still stands for it. */
export function firstPageOfSource(
  pages: readonly PageSchema[],
  sourceId: string,
): PageSchema | undefined {
  return pages.find((page) => page.source_id === sourceId);
}

/** The first page of the book that was cut from a scan, if any. */
export function firstPageOfScan(
  pages: readonly PageSchema[],
  scanId: string,
): PageSchema | undefined {
  return pages.find((page) => page.scan_id === scanId);
}
