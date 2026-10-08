import { CheckIcon, ChevronDownIcon, LoaderCircleIcon, PencilIcon } from 'lucide-react';
import { useState } from 'react';
import type { ContentType } from '@/api';
import { useUpdatePages } from '@/features/pages/actions';
import { describePageError } from '@/features/pages/errors';
import { useDetectContent } from '@/features/processing/queries';
import { describeContent, markOfContent, pagesToChange } from '@/features/workspace/content';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * What the open page shows, as a button of the canvas toolbar with the menu that changes it.
 *
 * The steps of a recipe have a condition, the pages of text or the pictures, and a page meets it by what it shows. The
 * program proposes the type from the share of the page that pictures cover and from the colour of the pictures, and the
 * reader's choice here is kept, so a later detection leaves the page as it is. The choice is made on the open page or on
 * the selected pages, and "Detect again" gives those pages back to the program.
 */

const labels = MESSAGES.processing.content;
const CONTENT_TYPES = Object.keys(MESSAGES.pages.contentTypes) as ContentType[];
const NO_PAGES: ReadonlySet<string> = new Set();

/** The pages a choice of the menu reaches. */
const Scope = { Page: 'page', Selected: 'selected' } as const;
type Scope = (typeof Scope)[keyof typeof Scope];

export function ContentTypeMenu({
  projectId,
  items,
  currentId,
  selected,
}: {
  projectId: string;
  /** Every page of the book with where it stands in the stage. */
  items: readonly StripItem[];
  /** The open page. */
  currentId: string | undefined;
  /** The pages selected in the grid. */
  selected: ReadonlySet<string>;
}): React.JSX.Element | null {
  const update = useUpdatePages(projectId);
  const detect = useDetectContent(projectId);
  const activeJobs = useActiveJobs(projectId);
  const [scope, setScope] = useState<Scope>(Scope.Page);
  // The choice made and not yet settled, which the button shows at once, since a button that waits for the manifest to be
  // read again jumps back to its old value for a moment
  const [pending, setPending] = useState<{ id: string; type: ContentType } | null>(null);
  const [open] = pagesToChange(items, NO_PAGES, currentId);
  if (open === undefined) {
    return null;
  }
  const selectedCount = pagesToChange(items, selected, undefined).length;
  const reaching = scope === Scope.Selected && selectedCount > 0 ? Scope.Selected : Scope.Page;
  const ids = pagesToChange(
    items,
    reaching === Scope.Selected ? selected : NO_PAGES,
    currentId,
  ).map((page) => page.id);
  const shown = pending?.id === open.id ? pending.type : open.content_type;
  const detecting = detect.isPending || (activeJobs.data?.length ?? 0) > 0;
  const scopes = [
    [Scope.Page, labels.thisPage, null],
    [Scope.Selected, labels.selectedPages, selectedCount],
  ] as const;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={update.isError || detect.isError ? 'text-status-failed' : undefined}
          title={describeContent(open)}
          data-testid="content-type-menu"
          data-content={shown}
          data-source={open.content_source}
        >
          <span aria-hidden="true">{MESSAGES.workspace.steps.marks[markOfContent(shown)]}</span>
          {MESSAGES.pages.contentTypes[shown]}
          {open.content_source === 'hand' ? (
            <PencilIcon aria-label={MESSAGES.pages.contentSources.hand} />
          ) : null}
          <ChevronDownIcon aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="top" data-testid="content-type-list">
        <DropdownMenuLabel className="text-xs text-muted-foreground uppercase">
          {labels.title}
        </DropdownMenuLabel>
        {CONTENT_TYPES.map((type) => (
          <DropdownMenuItem
            key={type}
            data-testid={`content-type-${type}`}
            aria-checked={shown === type}
            onSelect={() => {
              setPending({ id: open.id, type });
              update.mutate(
                { pageIds: ids, changes: { content_type: type } },
                { onSettled: () => setPending(null) },
              );
            }}
          >
            <span aria-hidden="true">{MESSAGES.workspace.steps.marks[markOfContent(type)]}</span>
            {MESSAGES.pages.contentTypes[type]}
            {open.content_source !== 'hand' && open.content_type === type ? (
              <span className="ml-auto text-xs text-muted-foreground">{labels.found}</span>
            ) : null}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs text-muted-foreground uppercase">
          {labels.applyTo}
        </DropdownMenuLabel>
        {scopes.map(([value, text, count]) => (
          <DropdownMenuItem
            key={value}
            data-testid={`content-scope-${value}`}
            aria-checked={reaching === value}
            disabled={count === 0}
            // The choice of pages is a setting of the menu, so picking it does not close the menu
            onSelect={(event) => {
              event.preventDefault();
              setScope(value);
            }}
          >
            <CheckIcon className={reaching === value ? 'opacity-100' : 'opacity-0'} />
            {text}
            {count === null ? null : (
              <span className="ml-auto text-xs text-muted-foreground tabular-nums">{count}</span>
            )}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={detecting}
          title={labels.detectHint}
          data-testid="content-type-detect"
          onSelect={(event) => {
            // The menu stays open while the program works, so a failure is read where it is shown
            event.preventDefault();
            detect.mutate({ path: { project_id: projectId }, body: { page_ids: ids } });
          }}
        >
          {detecting ? <LoaderCircleIcon className="animate-spin" /> : null}
          {detecting ? labels.detecting : labels.detect}
        </DropdownMenuItem>
        {update.isError ? <ErrorAlert message={describePageError(update.error)} /> : null}
        {detect.isError ? <ErrorAlert message={describeError(detect.error)} /> : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
