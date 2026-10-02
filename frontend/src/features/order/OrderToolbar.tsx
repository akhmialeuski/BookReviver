import { BookOpenIcon, ChevronDownIcon, HashIcon, LayoutGridIcon, PlusIcon } from 'lucide-react';
import { InsertMenu } from '@/features/order/InsertMenu';
import type { InsertSpec } from '@/features/order/insert';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The row above the grid of the Order stage: pages or spreads, the numbering, the Insert menu and the size of the
 * tiles.
 */

/** The width of a page tile in pixels: what the slider can reach and where it starts. */
export const TILE_SIZE = { min: 96, max: 288, step: 8, initial: 160 } as const;

export function OrderToolbar({
  spread,
  numbering,
  hasSelection,
  size,
  onSpread,
  onNumber,
  onInsert,
  onSize,
}: {
  spread: boolean;
  /** Whether the numbering panel is open. */
  numbering: boolean;
  hasSelection: boolean;
  size: number;
  onSpread: (spread: boolean) => void;
  onNumber: () => void;
  onInsert: (spec: InsertSpec) => void;
  onSize: (size: number) => void;
}): React.JSX.Element {
  const text = MESSAGES.order.toolbar;
  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b px-4 py-2"
      role="toolbar"
      aria-label={text.label}
    >
      <div className="flex rounded-md border p-0.5">
        <Button
          variant={spread ? 'ghost' : 'secondary'}
          size="sm"
          aria-pressed={!spread}
          onClick={() => onSpread(false)}
        >
          <LayoutGridIcon />
          {text.pages}
        </Button>
        <Button
          variant={spread ? 'secondary' : 'ghost'}
          size="sm"
          aria-pressed={spread}
          onClick={() => onSpread(true)}
        >
          <BookOpenIcon />
          {text.spreads}
        </Button>
      </div>
      <Button variant="outline" size="sm" aria-pressed={numbering} onClick={onNumber}>
        <HashIcon />
        {text.number}
      </Button>
      <InsertMenu hasSelection={hasSelection} onInsert={onInsert}>
        <Button variant="outline" size="sm">
          <PlusIcon />
          {text.insert}
          <ChevronDownIcon />
        </Button>
      </InsertMenu>
      <label className="ml-auto flex items-center gap-2 text-sm text-muted-foreground">
        {text.size}
        <input
          type="range"
          min={TILE_SIZE.min}
          max={TILE_SIZE.max}
          step={TILE_SIZE.step}
          value={size}
          onChange={(event) => onSize(Number(event.target.value))}
          className="w-32 accent-primary"
          data-testid="tile-size"
        />
      </label>
    </div>
  );
}
