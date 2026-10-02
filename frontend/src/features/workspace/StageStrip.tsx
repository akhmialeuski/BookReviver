import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useRef } from 'react';
import { PageTile } from '@/features/workspace/PageTile';
import type { PageFilter } from '@/features/workspace/params';
import { StripToolbar } from '@/features/workspace/StripToolbar';
import type { FilterCounts, StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * The strip of pages on the left of a stage screen: the filters, the switch to the grid, and a virtual list of the
 * pages with the result of the open stage as the picture of each.
 *
 * Only the rows in sight and a few around them are in the document, however long the book is, and the list scrolls
 * the open page into view when it changes, so the reader keeps their place when the page turns by a key.
 */

const ROW_ESTIMATE_PX = 200;
const OVERSCAN_ROWS = 6;

export function StageStrip({
  items,
  total,
  counts,
  filter,
  currentId,
  onFilter,
  onOpen,
  onGrid,
}: {
  /** The pages the filter lists, in book order. */
  items: readonly StripItem[];
  /** The pages of the book, which the filters narrow down. */
  total: number;
  counts: FilterCounts;
  filter: PageFilter;
  currentId: string | undefined;
  onFilter: (filter: PageFilter) => void;
  onOpen: (pageId: string) => void;
  onGrid: () => void;
}): React.JSX.Element {
  const scroller = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_ESTIMATE_PX,
    overscan: OVERSCAN_ROWS,
    getItemKey: (index) => items[index]?.page.id ?? index,
  });

  const currentIndex = items.findIndex((item) => item.page.id === currentId);
  useEffect(() => {
    if (currentIndex >= 0) {
      virtualizer.scrollToIndex(currentIndex, { align: 'auto' });
    }
  }, [currentIndex, virtualizer]);

  return (
    <div className="flex h-full flex-col" data-testid="page-strip">
      <StripToolbar
        total={total}
        shown={items.length}
        counts={counts}
        filter={filter}
        grid={false}
        onFilter={onFilter}
        onSwitchView={onGrid}
      />
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">
          {MESSAGES.workspace.strip.empty[filter]}
        </p>
      ) : (
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" data-testid="strip-scroll">
          <ol
            aria-label={MESSAGES.workspace.strip.title}
            className="relative w-full"
            style={{ height: virtualizer.getTotalSize() }}
          >
            {virtualizer.getVirtualItems().map((row) => {
              const item = items[row.index];
              return item === undefined ? null : (
                <li
                  key={row.key}
                  ref={virtualizer.measureElement}
                  data-index={row.index}
                  className="absolute top-0 left-0 w-full px-2 py-1"
                  style={{ transform: `translateY(${row.start}px)` }}
                >
                  <PageTile
                    item={item}
                    highlighted={item.page.id === currentId}
                    onClick={() => onOpen(item.page.id)}
                  />
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </div>
  );
}
