import { ChevronDownIcon, EyeIcon, LoaderCircleIcon, PlayIcon } from 'lucide-react';
import { useState } from 'react';
import type { StageRunBody } from '@/api';
import { useRunStage } from '@/features/processing/queries';
import { pageIdsFor, RunScope, scopeChoices, troubleOf } from '@/features/processing/scope';
import { undoesSplit } from '@/features/processing/split';
import { UnsplitDialog } from '@/features/processing/UnsplitDialog';
import { PreviewBlock, type Processing } from '@/features/processing/useProcessing';
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
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The foot of the panel of a processing stage: how many pages are out of date or failed, the preview of the open page,
 * and the run with the pages it goes over.
 *
 * A run goes by the saved recipe, so while the draft has changes the run waits for them to be saved, and a preview, which
 * goes by the draft, is what to use to try them. A run that would send a scan back to one page asks first, and carries the
 * confirmation the server wants.
 */

const labels = MESSAGES.processing;

const PREVIEW_BLOCKED: Record<PreviewBlock, string> = {
  [PreviewBlock.NoPage]: labels.footer.previewNoPage,
  [PreviewBlock.NoStep]: labels.footer.previewNoStep,
  [PreviewBlock.Invalid]: labels.footer.previewInvalid,
  [PreviewBlock.Split]: labels.footer.previewSplit,
};

export function RunControls({
  processing,
  items,
  current,
  selected,
}: {
  processing: Processing;
  items: readonly StripItem[];
  current: StripItem | undefined;
  selected: ReadonlySet<string>;
}): React.JSX.Element {
  const { projectId, stage, recipe, preview } = processing;
  const run = useRunStage(projectId, stage);
  const activeJobs = useActiveJobs(projectId);
  const [confirming, setConfirming] = useState<StageRunBody | null>(null);
  const trouble = troubleOf(items);
  const choices = scopeChoices(items, current?.page.id, selected);
  const anotherJobGoing = (activeJobs.data?.length ?? 0) > 0;
  const blocked = recipe === undefined || processing.dirty;

  const send = (body: StageRunBody): void =>
    run.mutate({ path: { project_id: projectId, stage }, body });

  const start = (scope: RunScope): void => {
    if (recipe === undefined) {
      return;
    }
    const ids = pageIdsFor(scope, items, current?.page.id, selected);
    const body: StageRunBody =
      ids === null ? { recipe_id: recipe.id } : { recipe_id: recipe.id, page_ids: ids };
    const affected =
      ids === null
        ? items.map((item) => item.page)
        : items.filter((item) => ids.includes(item.page.id)).map((item) => item.page);
    if (undoesSplit(recipe, affected)) {
      setConfirming(body);
    } else {
      send(body);
    }
  };

  const scopeLabel = (scope: RunScope, count: number): string => {
    switch (scope) {
      case RunScope.Page:
        return labels.scope.page(current?.page.label ?? '');
      case RunScope.Selected:
        return labels.scope.selected(count);
      case RunScope.Attention:
        return labels.scope.attention(count);
      case RunScope.All:
        return labels.scope.all(count);
    }
  };

  return (
    <div className="grid gap-3">
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" data-testid="run-summary">
        {trouble.stale === 0 && trouble.failed === 0 ? (
          <span className="text-muted-foreground">{labels.footer.allClear}</span>
        ) : (
          <>
            {trouble.stale === 0 ? null : (
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-status-attention" aria-hidden="true" />
                {labels.footer.outOfDate(trouble.stale)}
              </span>
            )}
            {trouble.failed === 0 ? null : (
              <span className="flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-status-failed" aria-hidden="true" />
                {labels.footer.failed(trouble.failed)}
              </span>
            )}
          </>
        )}
      </p>
      {processing.dirty ? (
        <p className="text-xs text-muted-foreground">{labels.save.saveFirst}</p>
      ) : anotherJobGoing ? (
        <p className="text-xs text-muted-foreground">{labels.footer.busy}</p>
      ) : null}
      <div className="flex gap-2">
        <Button
          variant={preview.on ? 'secondary' : 'outline'}
          className="flex-1"
          aria-pressed={preview.on}
          disabled={!preview.on && preview.blocked !== null}
          title={preview.blocked === null ? undefined : PREVIEW_BLOCKED[preview.blocked]}
          data-testid="preview-toggle"
          onClick={preview.toggle}
        >
          {preview.on && preview.working ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : (
            <EyeIcon />
          )}
          {preview.on ? labels.footer.previewOn : labels.footer.preview}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              className="flex-1"
              disabled={blocked || run.isPending || anotherJobGoing}
              data-testid="run-menu"
            >
              {run.isPending ? <LoaderCircleIcon className="animate-spin" /> : <PlayIcon />}
              {labels.footer.run}
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>{labels.scope.menu}</DropdownMenuLabel>
            {choices.map(({ scope, count }) => (
              <DropdownMenuItem
                key={scope}
                disabled={count === 0}
                data-testid={`run-${scope}`}
                onSelect={() => start(scope)}
              >
                {scopeLabel(scope, count)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {run.error === null ? null : <ErrorAlert message={describeError(run.error)} />}
      <UnsplitDialog
        open={confirming !== null}
        onCancel={() => setConfirming(null)}
        onConfirm={() => {
          if (confirming !== null) {
            send({ ...confirming, confirm_unsplit: true });
          }
          setConfirming(null);
        }}
      />
    </div>
  );
}
