import { useEffect, useRef } from 'react';
import type { PageSchema } from '@/api';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The collapsible column of thumbnails beside the viewer, with the pages that are on screen marked.
 *
 * It scrolls the first page of the view into sight whenever the view changes, so the reader never loses their place
 * in a long book, and a click on a thumbnail opens that page.
 */

export function ThumbnailPanel({
  pages,
  shownIds,
  onOpen,
}: {
  pages: readonly PageSchema[];
  /** Ids of the pages of the current view. */
  shownIds: ReadonlySet<string>;
  onOpen: (index: number) => void;
}): React.JSX.Element {
  const listRef = useRef<HTMLOListElement | null>(null);
  const firstShownId = pages.find((page) => shownIds.has(page.id))?.id;

  useEffect(() => {
    if (firstShownId === undefined) {
      return;
    }
    listRef.current
      ?.querySelector(`[data-page-id="${CSS.escape(firstShownId)}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [firstShownId]);

  return (
    <ol
      ref={listRef}
      aria-label={MESSAGES.viewer.panel.title}
      className="grid content-start gap-2 overflow-y-auto p-2"
      data-testid="page-panel"
    >
      {pages.map((page, index) => {
        const shown = shownIds.has(page.id);
        return (
          <li key={page.id} data-page-id={page.id}>
            <button
              type="button"
              onClick={() => onOpen(index)}
              aria-current={shown ? 'page' : undefined}
              aria-label={MESSAGES.viewer.panel.page(page.position + 1, page.label)}
              className={cn(
                'grid w-full gap-1 rounded-md p-1 text-left text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
                shown ? 'bg-accent ring-2 ring-primary' : 'hover:bg-accent/50',
              )}
            >
              <PageThumbnail page={page} alt="" />
              <span className="truncate text-center text-muted-foreground">
                {page.label === '' ? MESSAGES.pages.position(page.position + 1) : page.label}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
