import { useVirtualizer } from '@tanstack/react-virtual';
import { XIcon } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { PageTile } from '@/features/workspace/PageTile';
import { PageFilter } from '@/features/workspace/params';
import { StripToolbar } from '@/features/workspace/StripToolbar';
import type { FilterCounts, StripItem } from '@/features/workspace/strip';
import { useStripPlace } from '@/features/workspace/useStripPlace';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The grid of pages across the whole canvas, for picking many pages at once.
 *
 * It lists the same pages as the strip, in rows of as many tiles as the width holds, and keeps only the rows in sight
 * in the document. A click selects one page, a click with Ctrl or Cmd adds or removes one, a click with Shift selects
 * the range from the last page clicked, and a double click opens the page on the canvas.
 */

const TILE_MIN_WIDTH_PX = 144;
const ROW_ESTIMATE_PX = 220;
const OVERSCAN_ROWS = 3;

/** Follow the width of an element, which decides how many tiles a row holds. */
function useElementWidth(element: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const target = element.current;
    if (target === null || typeof ResizeObserver === 'undefined') {
      return;
    }
    const observer = new ResizeObserver(([entry]) => setWidth(entry?.contentRect.width ?? 0));
    observer.observe(target);
    return () => observer.disconnect();
  }, [element]);
  return width;
}

export function StageGrid({
  items,
  total,
  counts,
  filter,
  selected,
  onFilter,
  onSelect,
  onClearSelection,
  onOpen,
  onList,
  reasonOf,
  withWide = false,
}: {
  items: readonly StripItem[];
  total: number;
  counts: FilterCounts;
  filter: PageFilter;
  selected: ReadonlySet<string>;
  onFilter: (filter: PageFilter) => void;
  /** Called with the page clicked and the modifier keys held, for the screen to work out the new selection. */
  onSelect: (pageId: string, modifiers: { range: boolean; toggle: boolean }) => void;
  onClearSelection: () => void;
  onOpen: (pageId: string) => void;
  /** Go back to the strip and the canvas. */
  onList: () => void;
  /** Says why a page asks for a look; the Check filter writes it under the page. Absent for no reasons. */
  reasonOf?: (item: StripItem) => string | null;
  /** Whether the filter of the pages cut from wide scans is offered, which the Split stage has. */
  withWide?: boolean;
}): React.JSX.Element {
  const scroller = useRef<HTMLDivElement>(null);
  const width = useElementWidth(scroller);
  const columns = Math.max(Math.floor(width / TILE_MIN_WIDTH_PX), 1);
  const rows = Math.ceil(items.length / columns);
  const virtualizer = useVirtualizer({
    count: rows,
    getScrollElement: () => scroller.current,
    estimateSize: () => ROW_ESTIMATE_PX,
    overscan: OVERSCAN_ROWS,
  });
  const pageIds = useMemo(() => items.map((item) => item.page.id), [items]);
  useStripPlace(scroller, virtualizer, pageIds, columns, width > 0 && items.length > 0);

  return (
    <div className="flex size-full flex-col" data-testid="grid">
      <StripToolbar
        total={total}
        shown={items.length}
        counts={counts}
        filter={filter}
        grid
        withWide={withWide}
        onFilter={onFilter}
        onSwitchView={onList}
      />
      <div className="flex min-h-9 items-center gap-2 border-b px-3 text-sm">
        <span data-testid="grid-selection">{MESSAGES.workspace.grid.selected(selected.size)}</span>
        {selected.size > 0 ? (
          <Button variant="ghost" size="sm" onClick={onClearSelection}>
            <XIcon />
            {MESSAGES.workspace.grid.clear}
          </Button>
        ) : null}
      </div>
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">
          {MESSAGES.workspace.strip.empty[filter]}
        </p>
      ) : (
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto p-2">
          <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((row) => (
              <div
                key={row.key}
                ref={virtualizer.measureElement}
                data-index={row.index}
                className="absolute top-0 left-0 grid w-full gap-2"
                style={{
                  transform: `translateY(${row.start}px)`,
                  gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                }}
              >
                {items.slice(row.index * columns, (row.index + 1) * columns).map((item) => (
                  <PageTile
                    key={item.page.id}
                    item={item}
                    highlighted={selected.has(item.page.id)}
                    caption={filter === PageFilter.Check ? (reasonOf?.(item) ?? null) : null}
                    onClick={(event) =>
                      onSelect(item.page.id, {
                        range: event.shiftKey,
                        toggle: event.ctrlKey || event.metaKey,
                      })
                    }
                    onDoubleClick={() => onOpen(item.page.id)}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
