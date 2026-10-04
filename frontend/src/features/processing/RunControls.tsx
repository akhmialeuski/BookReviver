import { ChevronDownIcon, EyeIcon, LoaderCircleIcon, PlayIcon } from 'lucide-react';
import { useState } from 'react';
import type { RunMode } from '@/api';
import { OverwriteDialog } from '@/features/processing/OverwriteDialog';
import { troubleOf } from '@/features/processing/scope';
import { UnsplitDialog, UnsplitQuestion } from '@/features/processing/UnsplitDialog';
import { PreviewBlock, type Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import { useStageSummaries } from '@/features/workspace/queries';
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
 * With the active recipe shown the run names no recipe, so each page is processed by the variant pinned to it or the
 * rule of the book that matches it. With another variant shown the run is a trial of that variant on the pages.
 *
 * A run goes by the saved recipe, so while the draft has changes the run waits for them to be saved, and a preview, which
 * goes by the draft, is what to use to try them. A run that would send a scan back to one page asks first, and carries the
 * confirmation the server wants. The pages a run through some of the steps left short of the last say so, by the step.
 *
 * The run keeps the settings and the hand edits of the pages. The choice of a mode below the summary makes the next run
 * replace the hand settings or reset the page settings instead, and such a run says how many pages lose work first.
 */

const labels = MESSAGES.processing;

const RUN_MODES: readonly RunMode[] = ['keep', 'replace-hand', 'reset-page-settings'];

const PREVIEW_BLOCKED: Record<PreviewBlock, string> = {
  [PreviewBlock.NoPage]: labels.footer.previewNoPage,
  [PreviewBlock.NoStep]: labels.footer.previewNoStep,
  [PreviewBlock.Invalid]: labels.footer.previewInvalid,
  [PreviewBlock.Split]: labels.footer.previewSplit,
};

export function RunControls({
  processing,
  items,
  run,
}: {
  processing: Processing;
  items: readonly StripItem[];
  run: StageRun;
}): React.JSX.Element {
  const { projectId, stage, recipe, preview } = processing;
  const summaries = useStageSummaries(projectId);
  const trouble = troubleOf(items);
  const stopped = summaries.data?.find((entry) => entry.stage === stage)?.stopped ?? [];
  const [mode, setMode] = useState<RunMode>('keep');

  return (
    <div className="grid gap-3">
      {stopped.length === 0 || recipe === undefined ? null : (
        <ul className="grid gap-0.5 text-sm" data-testid="run-stopped">
          {stopped.map(({ through_step: step, pages }) => (
            <li key={step}>{labels.footer.stoppedAt(step + 1, recipe.steps.length, pages)}</li>
          ))}
        </ul>
      )}
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" data-testid="run-summary">
        {trouble.stale === 0 && trouble.failed === 0 ? (
          // Pages that stopped short are up to date but not done, which the lines above say
          stopped.length === 0 && (
            <span className="text-muted-foreground">{labels.footer.allClear}</span>
          )
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
      ) : run.busy ? (
        <p className="text-xs text-muted-foreground">{labels.footer.busy}</p>
      ) : null}
      <label className="grid gap-1 text-xs text-muted-foreground" title={labels.modes.hint}>
        {labels.modes.label}
        <select
          data-testid="run-mode"
          className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          value={mode}
          onChange={(event) => setMode(event.target.value as RunMode)}
        >
          {RUN_MODES.map((option) => (
            <option key={option} value={option}>
              {labels.modes.options[option]}
            </option>
          ))}
        </select>
      </label>
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
            <Button className="flex-1" disabled={run.disabled} data-testid="run-menu">
              {run.pending ? <LoaderCircleIcon className="animate-spin" /> : <PlayIcon />}
              {labels.footer.run}
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>{labels.scope.menu}</DropdownMenuLabel>
            {run.choices.map(({ scope, count }) => (
              <DropdownMenuItem
                key={scope}
                disabled={count === 0}
                data-testid={`run-${scope}`}
                onSelect={() => run.start(scope, undefined, mode)}
              >
                {run.describe(scope, count)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {run.error === null ? null : <ErrorAlert message={describeError(run.error)} />}
      <OverwriteDialog
        impact={run.overwriting}
        onConfirm={run.confirmOverwrite}
        onCancel={run.cancelOverwrite}
      />
      <UnsplitDialog
        question={run.confirming ? UnsplitQuestion.One : null}
        onCancel={run.cancel}
        onConfirm={run.confirm}
      />
    </div>
  );
}
