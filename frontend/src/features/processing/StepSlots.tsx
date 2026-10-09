import { TriangleAlertIcon } from 'lucide-react';
import { useState } from 'react';
import type { CarryOverSchema, ProcessorSchema } from '@/api';
import { isPlacement } from '@/features/editors/placement';
import { CarryOver } from '@/features/processing/CarryOver';
import { MeasureBook } from '@/features/processing/MeasureBook';
import { ParamsForm } from '@/features/processing/ParamsForm';
import type { PageValues } from '@/features/processing/pageSettings';
import type { StepDraft } from '@/features/processing/recipe';
import type { Processing } from '@/features/processing/useProcessing';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';

/**
 * What a processing stage puts in the `step`, `settings` and `page` slots of its panel for the step that is open: the
 * notes about the step itself, the form of its settings and the carry-over of a shape set by hand.
 *
 * The settings are the draft of the recipe, which the window of the gear and the save bar share, so a change made here
 * is the change made there. A run starts from the foot of the panel and not from here, and the changes and the results
 * of the step on the open page are not listed here but in the history that ends the panel of the stage.
 */

const labels = MESSAGES.workspace.stepPanel;
const stepLabels = MESSAGES.processing.steps;

/** The notes that go under the title of the open step: that it is off, and where its place in the order is wrong. */
export function StepNotes({
  processing,
  draft,
  enabled,
}: {
  processing: Processing;
  /** The step in the draft of the recipe, or undefined while the draft has none such. */
  draft: StepDraft | undefined;
  /** Whether the saved step is switched on. */
  enabled: boolean;
}): React.JSX.Element | null {
  const issues = draft === undefined ? [] : (processing.orderIssues.get(draft.id) ?? []);
  const issueKind = issues.some((issue) => issue.kind === 'required') ? 'required' : 'usual';
  if (enabled && issues.length === 0) {
    return null;
  }
  return (
    <>
      {enabled ? null : <p className="text-xs text-muted-foreground">{labels.off}</p>}
      {issues.length === 0 ? null : (
        <div className="grid gap-2" data-testid="step-order-details" data-kind={issueKind}>
          {issues.map((issue) => (
            <p
              key={`${issue.otherId}|${issue.reason}`}
              className={cn(
                'flex items-start gap-1.5 text-xs break-words',
                issue.kind === 'required' ? 'text-destructive' : 'text-status-attention',
              )}
            >
              <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {issue.reason}
            </p>
          ))}
          <Button
            variant="outline"
            size="sm"
            className="w-fit"
            title={stepLabels.order.restoreHint}
            data-testid="step-restore-order"
            onClick={processing.restoreOrder}
          >
            {stepLabels.order.restore}
          </Button>
        </div>
      )}
    </>
  );
}

/** The form of the settings of the open step, with the values pages have for each, and the measurement of the book. */
export function StepSettings({
  processing,
  draft,
  processor,
  values,
}: {
  processing: Processing;
  draft: StepDraft;
  processor: ProcessorSchema;
  /** The open page and what it and the parts of the pages have for the steps, or undefined when the book has no page. */
  values: PageValues | undefined;
}): React.JSX.Element {
  return (
    <>
      <ParamsForm
        processor={processor}
        params={draft.params}
        idPrefix="step-panel"
        values={
          values === undefined || draft.stepId === null
            ? undefined
            : { page: values, stepId: draft.stepId }
        }
        onChange={(params) => processing.change(draft.id, params)}
      />
      {isPlacement(draft.processorKey) ? (
        <MeasureBook processing={processing} step={draft} />
      ) : null}
    </>
  );
}

/** The carry of the shape the reader set by hand on the open page to other pages, with its choice to overwrite. */
export function StepCarry({
  processing,
  pageId,
  stepId,
  selected,
  result,
  onResult,
}: {
  processing: Processing;
  pageId: string;
  stepId: string;
  /** The pages selected in the grid, which a shape set by hand can be carried over to. */
  selected: ReadonlySet<string>;
  /** What the last carry-over of the step did, or null when there is none or it was taken back. */
  result: CarryOverSchema | null;
  /** Called with what a carry-over did, and with null once it was taken back. */
  onResult: (result: CarryOverSchema | null) => void;
}): React.JSX.Element {
  const [overwrite, setOverwrite] = useState(false);
  return (
    <div className="grid gap-1" data-testid="step-carry">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{labels.carry.hint}</span>
        <CarryOver
          processing={processing}
          pageId={pageId}
          stepId={stepId}
          title={labels.carry.title}
          selected={selected}
          overwrite={overwrite}
          result={result}
          onResult={onResult}
        />
      </div>
      <CheckboxField
        label={stepLabels.carry.overwrite}
        checked={overwrite}
        data-testid="step-carry-overwrite"
        onChange={(event) => setOverwrite(event.target.checked)}
      />
    </div>
  );
}
