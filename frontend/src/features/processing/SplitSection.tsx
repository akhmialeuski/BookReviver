import { WandSparklesIcon } from 'lucide-react';
import { useState } from 'react';
import { SegmentedRadio } from '@/features/pages/SegmentedRadio';
import {
  useDeleteEdit,
  useEdits,
  useRunInFlight,
  useRunStage,
  useSaveEdit,
} from '@/features/processing/queries';
import {
  autoRecipeOf,
  choiceForm,
  choiceOf,
  chosenIn,
  pagesOfScan,
  SPLIT_PROCESSOR,
  SplitChoice,
} from '@/features/processing/split';
import { UnsplitDialog, UnsplitQuestion } from '@/features/processing/UnsplitDialog';
import type { Processing } from '@/features/processing/useProcessing';
import { useActiveJobs } from '@/features/workspace/queries';
import type { StripItem } from '@/features/workspace/strip';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The choice the Split stage offers for the scan of the open page: one page or two, or what the automatic split decides.
 *
 * A choice is kept as an edit of the automatic split on the page that shows the scan, whole or as its left half, so it
 * stays through every later run of the stage, and the page is made again at once by a run on that page alone. "Auto"
 * deletes the edit and makes the page again, which returns the scan to what the automatic split decides.
 *
 * Going back to one page deletes the right page of the spread with its work, so it asks first, and only after the answer
 * does the run go out with the confirmation the server wants. Returning to the automatic split of a scan that is cut
 * asks the same, since the automatic split may keep that scan whole.
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
  const saveEdit = useSaveEdit(projectId, stage);
  const deleteEdit = useDeleteEdit(projectId, stage);
  const activeJobs = useActiveJobs(projectId);
  const runInFlight = useRunInFlight(projectId);
  const [asking, setAsking] = useState<UnsplitQuestion | null>(null);
  const scanId = current.page.scan_id;
  const scanPages =
    scanId === null
      ? []
      : pagesOfScan(
          items.map((item) => item.page),
          scanId,
        );
  // The page that shows the scan whole, or its left half, is the one the stage runs on and the edit belongs to
  const driver = scanPages[0];
  const edits = useEdits(projectId, driver?.id, stage);
  if (driver === undefined) {
    return null;
  }
  const recipe = autoRecipeOf(processing.recipes);
  const autoStep = recipe?.steps.find((step) => step.processor_key === SPLIT_PROCESSOR.auto);
  const choice = choiceOf(scanPages);
  const chosen = autoStep === undefined ? null : chosenIn(edits.data ?? [], autoStep.step_id);
  // A run sent from another control of the screen counts too, since its job is not on the list for a moment
  const busy = runInFlight || saveEdit.isPending || deleteEdit.isPending;
  const idle = (activeJobs.data?.length ?? 0) === 0 && !busy && !processing.dirty;
  const path = {
    project_id: projectId,
    page_id: driver.id,
    stage,
    step_id: autoStep?.step_id ?? '',
  };

  const recompute = (confirm: boolean): void => {
    if (recipe === undefined) {
      return;
    }
    run.mutate({
      path: { project_id: projectId, stage },
      body: confirm ? { page_ids: [driver.id], confirm_unsplit: true } : { page_ids: [driver.id] },
    });
  };
  const choose = async (value: SplitChoice, confirm: boolean): Promise<void> => {
    await saveEdit.mutateAsync({ path, body: choiceForm(value) });
    recompute(confirm);
  };
  const returnToAuto = async (confirm: boolean): Promise<void> => {
    await deleteEdit.mutateAsync({ path });
    recompute(confirm);
  };
  const change = (value: SplitChoice): void => {
    if (value === choice || !idle || recipe === undefined) {
      return;
    }
    if (value === SplitChoice.One && choice === SplitChoice.Two) {
      setAsking(UnsplitQuestion.One);
    } else {
      void choose(value, false).catch(() => undefined);
    }
  };
  const auto = (): void => {
    if (!idle) {
      return;
    }
    if (choice === SplitChoice.Two) {
      setAsking(UnsplitQuestion.Auto);
    } else {
      void returnToAuto(false).catch(() => undefined);
    }
  };
  const answer = (): void => {
    const question = asking;
    setAsking(null);
    const done =
      question === UnsplitQuestion.Auto ? returnToAuto(true) : choose(SplitChoice.One, true);
    void done.catch(() => undefined);
  };
  const error = saveEdit.error ?? deleteEdit.error ?? run.error;

  return (
    <section className="grid gap-3" aria-label={labels.scan} data-testid="split-section">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {labels.scan}
      </h3>
      <SegmentedRadio
        legend={labels.choice}
        hideLegend
        disabled={!idle || recipe === undefined}
        value={choice}
        options={[
          { value: SplitChoice.One, label: labels.onePage },
          { value: SplitChoice.Two, label: labels.twoPages },
        ]}
        onChange={change}
      />
      {recipe === undefined ? (
        <p className="text-sm text-muted-foreground">{labels.noRecipe}</p>
      ) : chosen === null ? (
        <p className="text-sm text-muted-foreground" data-testid="split-automatic">
          {labels.automatic}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 flex-1 text-sm text-muted-foreground" data-testid="split-chosen">
            {labels.chosen(chosen === SplitChoice.Two ? labels.twoPages : labels.onePage)}
          </p>
          <Button
            variant="outline"
            size="sm"
            disabled={!idle}
            data-testid="split-auto"
            onClick={auto}
          >
            <WandSparklesIcon />
            {labels.auto}
          </Button>
        </div>
      )}
      {error === null ? null : <ErrorAlert message={describeError(error)} />}
      <UnsplitDialog question={asking} onCancel={() => setAsking(null)} onConfirm={answer} />
    </section>
  );
}
