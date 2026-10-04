import {
  type Announcements,
  closestCenter,
  DndContext,
  DragOverlay,
  type DragStartEvent,
  KeyboardCode,
  KeyboardSensor,
  PointerSensor,
  type UniqueIdentifier,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  SortableContext,
  type SortingStrategy,
  sortableKeyboardCoordinates,
} from '@dnd-kit/sortable';
import { defaultRangeExtractor, useVirtualizer } from '@tanstack/react-virtual';
import { MousePointerClickIcon } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { PageSchema } from '@/api';
import { carriedBy, dropPlace } from '@/features/order/drag';
import { GapCard } from '@/features/order/GapCard';
import { columnsOf, layoutOf, rowOfCells, rowsOf } from '@/features/order/layout';
import { OrderTile, type TileClick } from '@/features/order/OrderTile';
import type { SectionSpan } from '@/features/order/sections';
import type { LabelGap } from '@/features/pages/gaps';
import { shortName } from '@/features/pages/names';
import type { PageAnchor } from '@/features/pages/order';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The grid of the Order stage: every page of the book on a sheet that scrolls, in pages or in spreads, with the cards
 * of the gaps in the printed numbers where the missing pages would stand.
 *
 * Pages are moved by dragging, singly or as the selected group, and by the keyboard through dnd-kit's own sensor.
 * Nothing shifts aside while a page is held: the tile the drag is over draws a bar on the side the pages would land
 * on, and a drop makes the move through `onMove`, which applies it to the manifest at once and puts it back if the
 * server refuses. The grid keeps no list of its own, so a refused move shows the order of the server again.
 *
 * The cells are cut into rows by the number of columns the width of the sheet holds, and only the rows in sight and a
 * few around them are in the document, however long the book is. The sortable list still names every page, but only the
 * tiles in the document are registered with dnd-kit, so a drag works on what is drawn and the auto-scroll of dnd-kit
 * brings the next rows in. The row of the held page stays in the document while it is held, so the drag never loses it.
 */

/** Pixels the pointer must travel before a press is a drag, so that a click still selects. */
const DRAG_DISTANCE_PX = 8;
const GRID_GAP_PX = 12;
/** Space left of the first row and on both sides of the rows. */
const GRID_PADDING_PX = 16;
/** What a row of tiles is taller than its pictures before it is measured: the captions and the gap under it. */
const ROW_EXTRA_PX = 96;
/** The height-to-width ratio of a page picture, for the guess of a row before it is measured. */
const PAGE_ASPECT = 4 / 3;
/** Rows kept in the document above and below the rows in sight. */
const OVERSCAN_ROWS = 2;

/** Tiles stay where they are while another is dragged; the drop bar says where the pages will go. */
const KEEP_IN_PLACE: SortingStrategy = () => null;

export interface FocusRequest {
  /** The `data-cell` of the tile or card to scroll into view. */
  cellId: string;
  /** Changes with every request, so asking for the same cell twice scrolls twice. */
  nonce: number;
}

export function OrderGrid({
  pages,
  gaps,
  selected,
  spread,
  size,
  previewLabels,
  sectionOfPage,
  addingGapKey,
  moveError,
  busy,
  focus,
  onDismissError,
  onSelect,
  onOpen,
  onMove,
  onAddMissing,
}: {
  pages: readonly PageSchema[];
  gaps: readonly LabelGap[];
  selected: ReadonlySet<string>;
  spread: boolean;
  /** Width of a page tile in pixels. */
  size: number;
  /** The labels a numbering in preview would write, by page, or undefined when none is previewed. */
  previewLabels: ReadonlyMap<string, string> | undefined;
  /** The pagination section of each page by its id, which colours the number of the page. */
  sectionOfPage: ReadonlyMap<string, SectionSpan>;
  /** The key of the gap whose placeholders are being added, or null. */
  addingGapKey: string | null;
  /** The words for the last move the server refused, or null. */
  moveError: string | null;
  /**
   * Whether the grid is waiting for the server: a change is in flight, the manifest is being read again, or the labels of
   * a numbering are being worked out. What the grid shows may be one step behind until this is false, and the new numbers
   * of a preview are dimmed for as long.
   */
  busy: boolean;
  focus: FocusRequest | null;
  onDismissError: () => void;
  onSelect: (pageId: string, click: TileClick) => void;
  onOpen: (pageId: string) => void;
  /** Called with the pages that were dragged and the place they were dropped on. */
  onMove: (pageIds: readonly string[], anchor: PageAnchor) => void;
  onAddMissing: (gap: LabelGap) => void;
}): React.JSX.Element {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [target, setTarget] = useState<PageAnchor | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const handledFocus = useRef<number | null>(null);
  const items = useMemo(() => layoutOf(pages, gaps, spread), [pages, gaps, spread]);
  const ids = useMemo(() => pages.map((page) => page.id), [pages]);
  const byId = useMemo(() => new Map(pages.map((page) => [page.id, page])), [pages]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: DRAG_DISTANCE_PX } }),
    // Space lifts and drops, Enter stays with the tile for selecting, and Escape cancels
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
      keyboardCodes: {
        start: [KeyboardCode.Space],
        cancel: [KeyboardCode.Esc],
        end: [KeyboardCode.Space],
      },
    }),
  );

  const cell = spread ? size * 2 + GRID_GAP_PX : size;
  const columns = columnsOf(width, cell, GRID_GAP_PX);
  const rows = useMemo(() => rowsOf(items, columns), [items, columns]);
  const rowOf = useMemo(() => rowOfCells(rows), [rows]);
  const activeRow = activeId === null ? undefined : rowOf.get(activeId);

  // The sheet is as wide as its scroller less the space on both sides, and a scrollbar narrows it
  useLayoutEffect(() => {
    const element = scroller.current;
    if (element === null) {
      return undefined;
    }
    const measure = () => setWidth(element.clientWidth - 2 * GRID_PADDING_PX);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const rangeExtractor = useCallback(
    (range: Parameters<typeof defaultRangeExtractor>[0]) => {
      const indexes = defaultRangeExtractor(range);
      if (activeRow === undefined || indexes.includes(activeRow)) {
        return indexes;
      }
      return [...indexes, activeRow].sort((left, right) => left - right);
    },
    [activeRow],
  );

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => Math.round(size * PAGE_ASPECT) + ROW_EXTRA_PX,
    overscan: OVERSCAN_ROWS,
    paddingStart: GRID_PADDING_PX,
    // Heights measured at another size or with another number of cells must not be reused, so the key names both
    getItemKey: (index) => `${size}:${columns}:${rows[index]?.[0]?.key ?? index}`,
    rangeExtractor,
  });

  useEffect(() => {
    if (focus === null || handledFocus.current === focus.nonce) {
      return;
    }
    const row = rowOf.get(focus.cellId);
    if (row !== undefined) {
      handledFocus.current = focus.nonce;
      virtualizer.scrollToIndex(row, { align: 'center' });
    }
  }, [focus, rowOf, virtualizer]);

  const nameOf = (id: string | number): string => {
    const page = byId.get(String(id));
    return page === undefined ? '' : shortName(page);
  };

  const placeOf = (event: {
    active: { id: UniqueIdentifier };
    over: { id: UniqueIdentifier } | null;
  }): PageAnchor | null => {
    const { active, over } = event;
    if (over === null) {
      return null;
    }
    return dropPlace(
      pages,
      carriedBy(selected, String(active.id)),
      String(active.id),
      String(over.id),
    );
  };

  const announcements: Announcements = {
    onDragStart: ({ active }) =>
      MESSAGES.order.drag.pickedUp(nameOf(active.id), carriedBy(selected, String(active.id)).size),
    onDragOver: (event) => {
      if (event.over === null) {
        return undefined;
      }
      return placeOf(event) === null
        ? MESSAGES.order.drag.overSelf
        : MESSAGES.order.drag.over(nameOf(event.over.id));
    },
    onDragEnd: (event) =>
      event.over === null || placeOf(event) === null
        ? MESSAGES.order.drag.cancelled
        : MESSAGES.order.drag.dropped(nameOf(event.over.id)),
    onDragCancel: () => MESSAGES.order.drag.cancelled,
  };

  const activePage = activeId === null ? undefined : byId.get(activeId);
  const carriedCount = activeId === null ? 0 : carriedBy(selected, activeId).size;

  return (
    <div
      className="group/grid flex min-h-0 flex-1 flex-col"
      aria-busy={busy}
      data-testid="order-grid"
    >
      {moveError === null ? null : (
        <div className="flex items-start gap-2 p-3 pb-0" data-testid="move-error">
          <div className="flex-1">
            <ErrorAlert message={moveError} />
          </div>
          <Button variant="ghost" size="sm" onClick={onDismissError}>
            {MESSAGES.order.grid.dismiss}
          </Button>
        </div>
      )}
      <div
        ref={scroller}
        className="min-h-0 flex-1 overflow-y-auto"
        data-testid="order-scroll"
        style={{ paddingInline: GRID_PADDING_PX }}
      >
        {pages.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">{MESSAGES.order.grid.empty}</p>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            accessibility={{
              announcements,
              screenReaderInstructions: { draggable: MESSAGES.order.drag.instructions },
            }}
            onDragStart={(event: DragStartEvent) => {
              setActiveId(String(event.active.id));
              setTarget(null);
            }}
            onDragOver={(event) => setTarget(placeOf(event))}
            onDragEnd={(event) => {
              const place = placeOf(event);
              if (place !== null) {
                onMove([...carriedBy(selected, String(event.active.id))], place);
              }
              setActiveId(null);
              setTarget(null);
            }}
            onDragCancel={() => {
              setActiveId(null);
              setTarget(null);
            }}
          >
            <SortableContext items={ids} strategy={KEEP_IN_PLACE}>
              <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((row) => (
                  <div
                    key={row.key}
                    ref={virtualizer.measureElement}
                    data-index={row.index}
                    className="absolute top-0 left-0 grid w-full items-start gap-x-3 pb-4"
                    style={{
                      transform: `translateY(${row.start}px)`,
                      gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                    }}
                  >
                    {(rows[row.index] ?? []).map((item) =>
                      item.kind === 'gap' ? (
                        <GapCard
                          key={item.key}
                          gap={item.gap}
                          busy={addingGapKey === item.key}
                          width={size}
                          onAdd={() => onAddMissing(item.gap)}
                        />
                      ) : (
                        <div
                          key={item.key}
                          className="flex gap-1"
                          data-testid="order-group"
                          data-empty-side={item.emptySide ?? undefined}
                        >
                          {item.emptySide === 'left' ? (
                            <span className="flex-1" aria-hidden="true" />
                          ) : null}
                          {item.pages.map((page) => (
                            <OrderTile
                              key={page.id}
                              page={page}
                              selected={selected.has(page.id)}
                              dropSide={target?.pageId === page.id ? target.side : null}
                              previewLabel={previewLabels?.get(page.id)}
                              section={sectionOfPage.get(page.id)}
                              onSelect={(click) => onSelect(page.id, click)}
                              onOpen={() => onOpen(page.id)}
                            />
                          ))}
                          {item.emptySide === 'right' ? (
                            <span className="flex-1" aria-hidden="true" />
                          ) : null}
                        </div>
                      ),
                    )}
                  </div>
                ))}
              </div>
            </SortableContext>
            <DragOverlay dropAnimation={null}>
              {activePage === undefined ? null : (
                <div className="relative" style={{ width: size }}>
                  <PageThumbnail page={activePage} alt="" className="shadow-lg" />
                  {carriedCount > 1 ? (
                    <span
                      className="absolute -top-2 -right-2 rounded-full bg-foreground px-2 py-0.5 text-xs font-medium text-background"
                      data-testid="drag-count"
                    >
                      {MESSAGES.order.drag.stack(carriedCount)}
                    </span>
                  ) : null}
                </div>
              )}
            </DragOverlay>
          </DndContext>
        )}
      </div>
      <div className="flex items-center gap-2 border-t px-4 py-2 text-xs text-muted-foreground">
        <MousePointerClickIcon className="size-4" aria-hidden="true" />
        <span>{MESSAGES.order.grid.hintDrag} ·</span>
        <kbd className="rounded border px-1.5 py-0.5">{MESSAGES.order.grid.shift}</kbd>
        <span>{MESSAGES.order.grid.hintRange} ·</span>
        <kbd className="rounded border px-1.5 py-0.5">{MESSAGES.order.grid.ctrl}</kbd>
        <span>{MESSAGES.order.grid.hintAdd}</span>
      </div>
    </div>
  );
}
