import { PlusIcon, TriangleAlertIcon } from 'lucide-react';
import { gapKey } from '@/features/order/layout';
import { formatNumber, type LabelGap, missingCount } from '@/features/pages/gaps';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The card that stands in the grid where the printed numbers jump: which pages seem to be missing, and the button
 * that adds a placeholder for each.
 *
 * The card is amber with a dashed border and a mark, so it is not told from a page by colour alone. It is a cell of
 * the grid and never a page, so it takes no part in dragging and no part in the positions of the pages.
 */

export function GapCard({
  gap,
  busy,
  width,
  onAdd,
}: {
  gap: LabelGap;
  /** Whether the placeholders of this gap are being added. */
  busy: boolean;
  /** The width of a page tile in pixels, which the card keeps even in a cell as wide as a spread. */
  width: number;
  onAdd: () => void;
}): React.JSX.Element {
  const first = formatNumber(gap.style, gap.firstMissing);
  const last = formatNumber(gap.style, gap.lastMissing);
  return (
    <div
      className="grid min-w-0 content-start gap-1 p-1 text-xs"
      style={{ maxWidth: width }}
      data-testid="gap-card"
      data-cell={gapKey(gap)}
      data-check="true"
    >
      <div className="flex aspect-[3/4] flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed border-status-attention bg-status-attention/10 p-2 text-center">
        <TriangleAlertIcon className="size-6 text-status-attention" aria-hidden="true" />
        <p className="text-sm font-medium">
          {MESSAGES.order.gap.title(first, last, missingCount(gap))}
        </p>
        <p className="text-muted-foreground">
          {MESSAGES.order.gap.jump(
            formatNumber(gap.style, gap.jumpFrom),
            formatNumber(gap.style, gap.jumpTo),
          )}
        </p>
      </div>
      <Button variant="outline" size="sm" disabled={busy} onClick={onAdd}>
        <PlusIcon />
        {busy ? MESSAGES.order.gap.adding : MESSAGES.order.gap.add}
      </Button>
    </div>
  );
}
