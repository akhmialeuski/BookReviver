import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useMemo, useRef } from 'react';
import { PageTile } from '@/features/workspace/PageTile';
import { PageFilter } from '@/features/workspace/params';
import { StripEmpty } from '@/features/workspace/StripEmpty';
import { StripToolbar } from '@/features/workspace/StripToolbar';
import type { PageListProps } from '@/features/workspace/strip';
import { useAfterPick } from '@/features/workspace/stripSheet';
import { useStripPlace } from '@/features/workspace/useStripPlace';
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
  currentId,
  onOpen,
  onGrid,
  reasonOf,
  ...list
}: PageListProps & {
  currentId: string | undefined;
  onOpen: (pageId: string) => void;
  onGrid: () => void;
}): React.JSX.Element {
  const scroller = useRef<HTMLDivElement>(null);
  const afterPick = useAfterPick();
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

  // Declared after the scroll to the open page, so a strip that opens at its place ends where the reader left it
  const pageIds = useMemo(() => items.map((item) => item.page.id), [items]);
  useStripPlace(scroller, virtualizer, pageIds, 1, items.length > 0);

  return (
    <div className="flex h-full flex-col" data-testid="page-strip">
      <StripToolbar {...list} shown={items.length} grid={false} onSwitchView={onGrid} />
      {items.length === 0 ? (
        <StripEmpty filter={list.filter} flagged={list.flagged} />
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
                    caption={list.filter === PageFilter.Check ? (reasonOf?.(item) ?? null) : null}
                    onClick={() => {
                      onOpen(item.page.id);
                      afterPick();
                    }}
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
