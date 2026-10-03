import { RulerIcon, UndoIcon } from 'lucide-react';
import { hasManualMargins, MARGINS_SOURCE, MarginsSource } from '@/features/processing/margins';
import { useMeasureBook, useRunInFlight } from '@/features/processing/queries';
import type { StepDraft } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { useActiveJobs } from '@/features/workspace/queries';
import { describeError } from '@/shared/http/problem';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { ErrorAlert } from '@/shared/ui/error-alert';

/**
 * The button in the panel of the normalize step that measures the book: the median line height of its pages and a page
 * size that holds the median block with margins, written into the settings of the step.
 *
 * The job writes the saved recipe, so a draft that was changed and not saved would be overwritten and out of step with what
 * the server holds, and the button waits for the draft to be saved. The server measures only while the book is not
 * processing something else, and the button is disabled while a job of the book is going.
 */

const labels = MESSAGES.processing.steps.measure;

export function MeasureBook({
  processing,
  step,
}: {
  processing: Processing;
  step: StepDraft;
}): React.JSX.Element {
  const { projectId, dirty } = processing;
  const measure = useMeasureBook(projectId);
  const activeJobs = useActiveJobs(projectId);
  const runInFlight = useRunInFlight(projectId);
  const busy = measure.isPending || runInFlight || (activeJobs.data?.length ?? 0) > 0;

  return (
    <div className="grid gap-2" data-testid="measure-book">
      <Button
        variant="outline"
        size="sm"
        className="w-fit"
        disabled={dirty || busy}
        title={dirty ? labels.saveFirst : labels.hint}
        data-testid="measure-book-button"
        onClick={() => measure.mutate({ path: { project_id: projectId } })}
      >
        <RulerIcon />
        {busy ? labels.working : labels.button}
      </Button>
      <p className="text-xs text-muted-foreground">{dirty ? labels.saveFirst : labels.hint}</p>
      {hasManualMargins(step.params) ? (
        <div className="grid gap-2" data-testid="manual-margins">
          <p className="text-xs text-muted-foreground">{labels.manualMargins}</p>
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            title={labels.useMeasuredHint}
            data-testid="use-measured-margins"
            onClick={() =>
              processing.change(step.id, {
                ...step.params,
                [MARGINS_SOURCE]: MarginsSource.Measured,
              })
            }
          >
            <UndoIcon />
            {labels.useMeasured}
          </Button>
        </div>
      ) : null}
      {measure.error === null ? null : <ErrorAlert message={describeError(measure.error)} />}
    </div>
  );
}
