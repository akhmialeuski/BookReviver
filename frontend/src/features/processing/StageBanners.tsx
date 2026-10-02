import { LightbulbIcon, RefreshCwIcon, ScissorsIcon } from 'lucide-react';
import { useState } from 'react';
import type { Stage } from '@/api';
import { useAllScans, useRunInFlight, useRunStage } from '@/features/processing/queries';
import { cutterOf, offerFor, wideScanIds } from '@/features/processing/split';
import type { Processing } from '@/features/processing/useProcessing';
import { stageBefore } from '@/features/stages/stages';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The banners above the canvas of a processing stage.
 *
 * One says the pages are out of date because a stage before this one changed after they were made, and offers to run the
 * stage again on them. The other, on the Split stage only, says that some scans are wider than tall and look like open
 * books, and offers to cut them all with one run of the recipe that cuts, after which the cut is checked on each.
 */

const labels = MESSAGES.processing;

/** What a stage did to its pages, for the sentence that says they are out of date. */
const VERBS: Partial<Record<Stage, string>> = labels.stale.verbs;

export function StageBanners({
  processing,
  items,
}: {
  processing: Processing;
  items: readonly StripItem[];
}): React.JSX.Element | null {
  const { projectId, stage, recipe } = processing;
  const run = useRunStage(projectId, stage);
  const activeJobs = useActiveJobs(projectId);
  const scans = useAllScans(projectId, stage === 'page-split');
  const [dismissed, setDismissed] = useState(false);
  const before = stageBefore(stage);
  const runInFlight = useRunInFlight(projectId);
  const idle = (activeJobs.data?.length ?? 0) === 0 && !runInFlight && !processing.dirty;

  const staleIds = items
    .filter((item) => item.row?.status === 'stale' && item.page.origin !== 'placeholder')
    .map((item) => item.page.id);
  const offer = offerFor(
    items.map((item) => item.page),
    wideScanIds(scans.data ?? []),
  );
  const cutter = cutterOf(processing.recipes);
  const showStale = recipe !== undefined && staleIds.length > 0;
  const showSplit = stage === 'page-split' && offer.toCut > 0 && cutter !== undefined && !dismissed;
  if (!showStale && !showSplit && run.error === null) {
    return null;
  }

  return (
    <div className="grid gap-2 p-2">
      {showSplit ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-950"
          data-testid="split-banner"
        >
          <LightbulbIcon className="size-4 shrink-0" aria-hidden="true" />
          <p className="min-w-0 flex-1">
            {labels.split.banner(offer.wide, offer.split, offer.toCut)}
          </p>
          <Button
            size="sm"
            disabled={!idle}
            data-testid="split-banner-cut"
            onClick={() =>
              run.mutate({
                path: { project_id: projectId, stage },
                body: { recipe_id: cutter.id, page_ids: offer.pageIds },
              })
            }
          >
            <ScissorsIcon />
            {labels.split.cut(offer.toCut)}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setDismissed(true)}>
            {labels.split.notNow}
          </Button>
        </div>
      ) : null}
      {showStale ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-status-attention/60 bg-status-attention/10 px-4 py-3 text-sm"
          data-testid="stale-banner"
        >
          <RefreshCwIcon className="size-4 shrink-0 text-status-attention" aria-hidden="true" />
          <p className="min-w-0 flex-1">
            {labels.stale.title(
              before === null ? '' : MESSAGES.stages.names[before],
              VERBS[stage] ?? labels.stale.otherVerb,
            )}
          </p>
          <Button
            size="sm"
            variant="outline"
            disabled={!idle}
            data-testid="stale-banner-run"
            onClick={() =>
              run.mutate({
                path: { project_id: projectId, stage },
                body: { recipe_id: recipe.id, page_ids: staleIds },
              })
            }
          >
            {labels.stale.rerun(staleIds.length)}
          </Button>
        </div>
      ) : null}
      {run.error === null ? null : <ErrorAlert message={describeError(run.error)} />}
    </div>
  );
}
