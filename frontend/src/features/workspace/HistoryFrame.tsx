import { ChevronDownIcon, ClockIcon } from 'lucide-react';
import { useHistoryOpen } from '@/features/workspace/historyOpen';
import { PanelHeading } from '@/features/workspace/PanelHeading';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Badge } from '@/shared/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';

/**
 * The frame of the history of the open page, the last element of the panel of every stage: a collapsible shell with a
 * header, a chip with the number of events, and slots for what the stage puts in it.
 *
 * The frame knows nothing of what a stage keeps. The header shows a chevron, the clock, the title and the chip, and
 * opens and closes the section, and whether it is open is remembered once for every stage. A section with a reason is
 * grey and cannot be opened, and says why under its header. The content, which is the list of events, stands under the
 * filters when the section is open, and the notice stands under the header whether it is open or not.
 */

const labels = MESSAGES.workspace.history;

export function HistoryFrame({
  count,
  reason,
  filters,
  notice,
  children,
}: {
  /** The number of events the section holds, or null while it is not known, which leaves the chip out. */
  count: number | null;
  /** Why the section is grey and cannot be opened, or null when it can. */
  reason: string | null;
  /** The row that narrows the list, which stands above it. */
  filters?: React.ReactNode;
  /** What stands under the header whether the section is open or not, such as an error. */
  notice?: React.ReactNode;
  /** The list of events, which is drawn only while the section is open. */
  children?: React.ReactNode;
}): React.JSX.Element {
  const [open, setOpen] = useHistoryOpen();
  const disabled = reason !== null;

  return (
    <Collapsible
      open={open && !disabled}
      disabled={disabled}
      onOpenChange={setOpen}
      role="region"
      aria-label={labels.title}
      aria-disabled={disabled}
      className={cn(
        'grid min-w-0 grid-cols-1 gap-2 rounded-md border bg-muted/30 p-3',
        disabled && 'opacity-60',
      )}
      data-testid="page-history"
    >
      <CollapsibleTrigger
        className="group flex w-full min-w-0 items-center gap-2 text-left disabled:cursor-not-allowed"
        data-testid="page-history-toggle"
      >
        <ChevronDownIcon
          className="size-4 shrink-0 -rotate-90 text-muted-foreground transition-transform group-data-[state=open]:rotate-0"
          aria-hidden="true"
        />
        <ClockIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <PanelHeading className="min-w-0">{labels.title}</PanelHeading>
        {disabled || count === null ? null : (
          <Badge variant="outline" data-testid="page-history-count">
            {labels.count(count)}
          </Badge>
        )}
      </CollapsibleTrigger>
      {reason === null ? null : (
        <p className="text-xs break-words text-muted-foreground" data-testid="page-history-reason">
          {reason}
        </p>
      )}
      <CollapsibleContent className="grid min-w-0 grid-cols-1 gap-2">
        {filters}
        {children}
      </CollapsibleContent>
      {notice}
    </Collapsible>
  );
}
