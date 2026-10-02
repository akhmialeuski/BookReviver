import { useState } from 'react';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';
import { useRunStage } from '@/features/processing/queries';
import { choiceOf, pagesOfScan, recipeFor, SplitChoice } from '@/features/processing/split';
import { UnsplitDialog } from '@/features/processing/UnsplitDialog';
import type { Processing } from '@/features/processing/useProcessing';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The choice the Split stage offers for the scan of the open page: one page or two.
 *
 * "Two pages" runs the recipe that cuts on the page of the scan, and "One page" runs the recipe that keeps it whole on the
 * pages of the scan. Going back to one page deletes the right page of the spread with its work, so it asks first, and
 * only after the answer does the run go out with the confirmation the server wants.
 */

const labels = MESSAGES.processing.split;

export function SplitSection({
  processing,
  items,
  current,
}: {
  processing: Processing;
  items: readonly StripItem[];
  current: StripItem;
}): React.JSX.Element | null {
  const { projectId, stage } = processing;
  const run = useRunStage(projectId, stage);
  const activeJobs = useActiveJobs(projectId);
  const [asking, setAsking] = useState(false);
  const scanId = current.page.scan_id;
  if (scanId === null) {
    return null;
  }
  const scanPages = pagesOfScan(
    items.map((item) => item.page),
    scanId,
  );
  const choice = choiceOf(scanPages);
  const pageIds = scanPages.map((page) => page.id);
  const idle = (activeJobs.data?.length ?? 0) === 0 && !run.isPending && !processing.dirty;
  const missing = (value: SplitChoice): boolean =>
    recipeFor(processing.recipes, value) === undefined;

  const go = (value: SplitChoice, confirm: boolean): void => {
    const recipe = recipeFor(processing.recipes, value);
    if (recipe === undefined) {
      return;
    }
    run.mutate({
      path: { project_id: projectId, stage },
      body: confirm
        ? { recipe_id: recipe.id, page_ids: pageIds, confirm_unsplit: true }
        : { recipe_id: recipe.id, page_ids: pageIds },
    });
  };
  const change = (value: SplitChoice): void => {
    if (value === choice || !idle) {
      return;
    }
    if (value === SplitChoice.One) {
      setAsking(true);
    } else {
      go(value, false);
    }
  };

  return (
    <section className="grid gap-3" aria-label={labels.scan} data-testid="split-section">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.scan}
      </h3>
      <SegmentedRadio
        legend={labels.choice}
        hideLegend
        value={choice}
        options={[
          { value: SplitChoice.One, label: labels.onePage },
          { value: SplitChoice.Two, label: labels.twoPages },
        ]}
        onChange={change}
      />
      {missing(SplitChoice.One) || missing(SplitChoice.Two) ? (
        <p className="text-sm text-muted-foreground">{labels.noRecipe}</p>
      ) : null}
      {run.error === null ? null : <ErrorAlert message={describeError(run.error)} />}
      <UnsplitDialog
        open={asking}
        onCancel={() => setAsking(false)}
        onConfirm={() => {
          setAsking(false);
          go(SplitChoice.One, true);
        }}
      />
    </section>
  );
}
