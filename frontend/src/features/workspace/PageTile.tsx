import { CircleXIcon, EyeOffIcon, TriangleAlertIcon } from 'lucide-react';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { PAGE_STATUS_TONE } from '@/features/stages/stages';
import { isLeftOut, type StripItem, thumbnailOf } from '@/features/workspace/strip';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * One page of the strip or of the grid: the picture of the result of the stage, the page label, the dot of the state
 * and the mark of what asks for a look.
 *
 * The dot has the colour of the state wherever the state is drawn, and the state is also a word that is read out
 * and a mark in the corner of the picture for a failure, a page to check and a page left out, so the colour is never
 * the only signal.
 */

export function PageTile({
  item,
  highlighted,
  onClick,
  onDoubleClick,
}: {
  item: StripItem;
  /** Whether the page is the one open on the canvas or one of the selected pages. */
  highlighted: boolean;
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDoubleClick?: () => void;
}): React.JSX.Element {
  const { page, row } = item;
  const label = page.label === '' ? MESSAGES.pages.position(page.position + 1) : page.label;
  const reason = row?.review ?? null;
  const status = row === undefined ? null : MESSAGES.stages.pageStatus[row.status];

  return (
    <button
      type="button"
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      aria-pressed={highlighted}
      aria-label={MESSAGES.viewer.panel.page(page.position + 1, page.label)}
      data-testid="strip-page"
      data-page-id={page.id}
      className={cn(
        'grid w-full gap-1 rounded-md p-1 text-left text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
        highlighted ? 'bg-accent ring-2 ring-primary' : 'hover:bg-accent/50',
      )}
    >
      <span className="relative block">
        <PageThumbnail page={page} src={thumbnailOf(item)} alt="" />
        {row?.status === 'failed' ? (
          <CircleXIcon
            className="absolute top-1 right-1 size-4 rounded-full bg-background text-status-failed"
            aria-hidden="true"
          />
        ) : reason !== null ? (
          <TriangleAlertIcon
            className="absolute top-1 right-1 size-4 rounded-full bg-background p-0.5 text-status-attention"
            aria-hidden="true"
          />
        ) : isLeftOut(item) ? (
          <EyeOffIcon
            className="absolute top-1 right-1 size-4 rounded-full bg-background p-0.5 text-muted-foreground"
            aria-hidden="true"
          />
        ) : null}
      </span>
      <span className="flex items-center justify-center gap-1.5 text-muted-foreground">
        {row === undefined ? null : (
          <span
            className={cn('size-2 shrink-0 rounded-full', PAGE_STATUS_TONE[row.status])}
            aria-hidden="true"
          />
        )}
        <span className="truncate">{label}</span>
        {status === null ? null : <span className="sr-only">{status}</span>}
        {reason === null ? null : <span className="sr-only">{MESSAGES.stages.review[reason]}</span>}
        {isLeftOut(item) ? (
          <span className="sr-only">{MESSAGES.workspace.strip.leftOut}</span>
        ) : null}
      </span>
    </button>
  );
}
