import {
  CircleXIcon,
  EyeOffIcon,
  PencilIcon,
  ThumbsDownIcon,
  TriangleAlertIcon,
} from 'lucide-react';
import { PageThumbnail } from '@/features/pages/PageThumbnail';
import { PAGE_STATUS_TONE } from '@/features/stages/stages';
import { describeContent, markOfContent } from '@/features/workspace/content';
import { isLeftOut, isMarkedBad, type StripItem, thumbnailOf } from '@/features/workspace/strip';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * One page of the strip or of the grid: the picture of the result of the stage, the page label, the dot of the state,
 * the mark of what the page shows and the mark of what asks for a look.
 *
 * The dot has the colour of the state wherever the state is drawn, and the state is also a word that is read out
 * and a mark in the corner of the picture for a failure, a page to check and a page left out, so the colour is never
 * the only signal. A page whose result is marked bad has a mark of its own in the other corner.
 */

export function PageTile({
  item,
  highlighted,
  caption = null,
  onClick,
  onDoubleClick,
}: {
  item: StripItem;
  /** Whether the page is the one open on the canvas or one of the selected pages. */
  highlighted: boolean;
  /** Why the page asks for a look, written under its label, or null for no line. */
  caption?: string | null;
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
        <span
          className="absolute bottom-1 left-1 flex items-center gap-1 rounded-full bg-background/90 px-1.5 py-0.5 text-[10px] leading-none shadow-xs"
          data-testid="strip-content"
          data-content={page.content_type}
          data-source={page.content_source}
          title={describeContent(page)}
        >
          <span aria-hidden="true">
            {MESSAGES.workspace.steps.marks[markOfContent(page.content_type)]}
          </span>
          {MESSAGES.workspace.strip.content.short[page.content_type] === '' ? null : (
            <span aria-hidden="true">
              {MESSAGES.workspace.strip.content.short[page.content_type]}
            </span>
          )}
          {page.content_source === 'hand' ? (
            <PencilIcon className="size-2.5 shrink-0" aria-hidden="true" />
          ) : null}
        </span>
        {row?.status === 'failed' ? (
          <span
            className="absolute top-1 right-1 flex"
            title={MESSAGES.stages.pageStatus.failed}
            data-testid="strip-failed"
          >
            <CircleXIcon
              className="size-4 rounded-full bg-background text-status-failed"
              aria-hidden="true"
            />
          </span>
        ) : reason !== null ? (
          <span
            className="absolute top-1 right-1 flex"
            title={MESSAGES.stages.review[reason]}
            data-testid="strip-review"
          >
            <TriangleAlertIcon
              className="size-4 rounded-full bg-background p-0.5 text-status-attention"
              aria-hidden="true"
            />
          </span>
        ) : isLeftOut(item) ? (
          <EyeOffIcon
            className="absolute top-1 right-1 size-4 rounded-full bg-background p-0.5 text-muted-foreground"
            aria-hidden="true"
          />
        ) : null}
        {isMarkedBad(item) ? (
          <ThumbsDownIcon
            className="absolute right-1 bottom-1 size-4 rounded-full bg-background p-0.5 text-status-failed"
            data-testid="strip-marked-bad"
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
        <span className="sr-only">{describeContent(page)}</span>
        {isLeftOut(item) ? (
          <span className="sr-only">{MESSAGES.workspace.strip.leftOut}</span>
        ) : null}
        {isMarkedBad(item) ? (
          <span className="sr-only">{MESSAGES.workspace.strip.markedBad}</span>
        ) : null}
      </span>
      {caption === null ? null : (
        <span
          className={cn(
            'text-center text-[11px] leading-tight',
            row?.status === 'failed' ? 'text-status-failed' : 'text-status-attention',
          )}
          data-testid="strip-reason"
        >
          {caption}
        </span>
      )}
    </button>
  );
}
