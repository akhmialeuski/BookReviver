import { useSortable } from '@dnd-kit/sortable';
import { CheckIcon, EyeOffIcon, PencilIcon, SquareDashedIcon } from 'lucide-react';
import { useRef } from 'react';
import type { PageSchema } from '@/api';
import type { SectionSpan } from '@/features/order/sections';
import { sectionName } from '@/features/order/sections';
import { AnchorSide } from '@/features/pages/order';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';

/**
 * One page of the Order grid: its picture, printed number, kind and place in the book, which a click selects and a
 * drag or the keyboard moves.
 *
 * The tile is a sortable item of dnd-kit, but the grid never shifts tiles aside while a page is dragged. Instead the
 * tile the drag is over draws a blue bar on the side the pages would land on. A click selects the page, and with
 * Shift or Ctrl it extends or adds to the selection, and so does Enter on the focused tile. Space picks the tile up
 * for the keyboard, since the sensor is set to listen for it. While a numbering is previewed, a tile whose number
 * changes shows the old number struck out and the new one in blue.
 *
 * The picture is the page at the Order stage, which the server gives in the rows of the stage: the leaf chosen for a
 * blank page, else what the stage reads, and never the result of a later stage that the page ran through before.
 *
 * The number stands in a pill ringed in the colour of the section the page belongs to, so the boundary between two
 * sections is visible along the grid, and a number written by hand carries a pencil.
 */

export interface TileClick {
  /** Shift: extend the selection from the last page selected. */
  range: boolean;
  /** Ctrl or Cmd: add the page to the selection or take it out. */
  toggle: boolean;
}

export function OrderTile({
  page,
  thumbnail,
  selected,
  dropSide,
  previewLabel,
  section,
  onSelect,
  onOpen,
}: {
  page: PageSchema;
  /** The thumbnail of the page at the Order stage, or null while the page has no picture there. */
  thumbnail: string | null;
  selected: boolean;
  /** The side a bar is drawn on while a drag is over this tile, or null. */
  dropSide: AnchorSide | null;
  /** The label a numbering in preview would give the page, which is undefined when it gives none. */
  previewLabel: string | undefined;
  /** The pagination section the page belongs to, or undefined for a page before the first section. */
  section: SectionSpan | undefined;
  onSelect: (click: TileClick) => void;
  onOpen: () => void;
}): React.JSX.Element {
  const { attributes, listeners, setNodeRef, isDragging } = useSortable({ id: page.id });
  const { onKeyDown: liftWithKeyboard, ...dragListeners } = listeners ?? {};
  const lastKey = useRef('');
  const missing = page.origin === 'placeholder';
  const changed = previewLabel !== undefined && previewLabel !== page.label;

  return (
    <button
      ref={setNodeRef}
      type="button"
      {...attributes}
      {...dragListeners}
      aria-pressed={selected}
      aria-label={MESSAGES.viewer.panel.page(page.position + 1, page.label)}
      onClick={(event) => {
        // Space lifts the tile, and a browser may still send the click of a pressed button when the key is released
        if (event.detail === 0 && lastKey.current === 'Space') {
          return;
        }
        onSelect({ range: event.shiftKey, toggle: event.ctrlKey || event.metaKey });
      }}
      onDoubleClick={onOpen}
      onKeyDown={(event) => {
        lastKey.current = event.code;
        liftWithKeyboard?.(event);
      }}
      data-testid="order-tile"
      data-page-id={page.id}
      data-cell={page.id}
      data-selected={selected}
      data-section-id={section?.section.id}
      data-check={missing && page.included ? 'true' : undefined}
      className={cn(
        'relative grid min-w-0 flex-1 cursor-pointer gap-1 rounded-md p-1 text-xs outline-none select-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
        selected ? 'bg-accent ring-2 ring-blue-500' : 'hover:bg-accent/50',
        isDragging ? 'opacity-40' : '',
      )}
    >
      {dropSide === null ? null : (
        <span
          aria-hidden="true"
          data-testid="drop-bar"
          data-side={dropSide}
          className={cn(
            'absolute inset-y-0 z-10 w-1 rounded-full bg-blue-500',
            dropSide === AnchorSide.Before ? '-left-1' : '-right-1',
          )}
        />
      )}
      <span className="relative block">
        {missing && thumbnail === null ? (
          <span className="flex aspect-[3/4] flex-col items-center justify-center gap-1 rounded-md border border-dashed bg-muted/50 text-muted-foreground">
            <SquareDashedIcon className="size-5" aria-hidden="true" />
            {MESSAGES.order.tile.missing}
          </span>
        ) : (
          <PageThumbnail page={page} src={thumbnail} alt="" />
        )}
        {selected ? (
          <CheckIcon
            className="absolute top-1 left-1 size-4 rounded-full bg-blue-500 p-0.5 text-white"
            aria-hidden="true"
          />
        ) : null}
        {page.included ? null : (
          <EyeOffIcon
            className="absolute top-1 right-1 size-4 rounded-full bg-background p-0.5 text-muted-foreground"
            aria-hidden="true"
          />
        )}
      </span>
      <span className="flex items-center justify-center gap-1.5">
        <NumberText
          label={page.label}
          manual={page.label_manual}
          section={section}
          previewLabel={changed ? previewLabel : undefined}
        />
        <Badge variant="outline" className="shrink-0 font-normal">
          {missing ? MESSAGES.order.tile.missing : MESSAGES.pages.kinds[page.kind]}
        </Badge>
      </span>
      <span className="text-center text-muted-foreground">
        {MESSAGES.order.tile.position(page.position + 1)}
        {page.included ? null : <span className="block">{MESSAGES.order.tile.leftOut}</span>}
      </span>
    </button>
  );
}

/** The printed number of a page, or the words for a page without one, or the old and the new number of a preview. */
function NumberText({
  label,
  manual,
  section,
  previewLabel,
}: {
  label: string;
  /** Whether the number was written by hand, which no section takes back. */
  manual: boolean;
  section: SectionSpan | undefined;
  /** The label a numbering would write, set only when it differs from the label the page has. */
  previewLabel: string | undefined;
}): React.JSX.Element {
  const old = label === '' ? MESSAGES.order.tile.noNumber : MESSAGES.order.tile.printed(label);
  if (previewLabel === undefined) {
    const name = section === undefined ? undefined : sectionName(section);
    return (
      <span
        className={cn(
          'inline-flex min-w-0 items-center gap-1 rounded-full border-2 px-2 font-medium',
          section?.tone.ring ?? 'border-transparent',
          label === '' ? 'text-muted-foreground' : '',
        )}
        title={name === undefined ? undefined : MESSAGES.order.tile.section(name)}
        data-testid="page-number"
        data-manual={manual && label !== '' ? 'true' : undefined}
      >
        <span className="truncate">{old}</span>
        {manual && label !== '' ? (
          <>
            <PencilIcon className="size-3 shrink-0" aria-hidden="true" />
            <span className="sr-only">{MESSAGES.order.tile.manual}</span>
          </>
        ) : null}
      </span>
    );
  }
  return (
    <span className="flex min-w-0 items-baseline gap-1 font-medium">
      <s className="truncate text-muted-foreground" data-testid="old-number">
        {label === '' ? MESSAGES.order.tile.noNumber : label}
      </s>
      <span
        className="truncate text-blue-600 group-aria-busy/grid:opacity-50"
        data-testid="new-number"
      >
        {previewLabel === '' ? MESSAGES.order.tile.noNumber : previewLabel}
      </span>
      <span className="sr-only">
        {MESSAGES.order.tile.renumbered(
          label === '' ? MESSAGES.order.tile.noNumber : label,
          previewLabel === '' ? MESSAGES.order.tile.hidden : previewLabel,
        )}
      </span>
    </span>
  );
}
