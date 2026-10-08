import { TriangleAlertIcon } from 'lucide-react';
import { useState } from 'react';
import { EditorControls } from '@/features/editors/EditorControls';
import { isPlacement } from '@/features/editors/placement';
import type { EditorSession } from '@/features/editors/session';
import { CarryOver } from '@/features/processing/CarryOver';
import { MeasureBook } from '@/features/processing/MeasureBook';
import { ParamsForm } from '@/features/processing/ParamsForm';
import type { PageValues } from '@/features/processing/pageSettings';
import { readResult } from '@/features/processing/results';
import type { Processing } from '@/features/processing/useProcessing';
import type { BarStep } from '@/features/workspace/steps';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';

/**
 * The section of the panel for the step that is open, which is where a step of a stage with a bar is set and looked at:
 * its settings, with the values the open page and the parts of the pages have for each, where it stands in the order and
 * what is wrong with that, and what it did on the open page. A run starts from the foot of the panel and not from here.
 *
 * It stands above the sections of the recipe and of the open page and takes none of them away. The settings are the
 * draft of the recipe, which the window of the gear and the save bar share, so a change made here is the change made
 * there. The changes and the results of the step on the open page are not listed here but in the history that ends the
 * panel of the stage.
 */

const labels = MESSAGES.workspace.stepPanel;
const stepLabels = MESSAGES.processing.steps;

const NO_PAGES: ReadonlySet<string> = new Set();

function Heading({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
      {children}
    </h3>
  );
}

export function StepPanel({
  processing,
  workspace,
  step,
  pageLabel,
  pageId,
  values,
  selected = NO_PAGES,
  editor,
}: {
  processing: Processing;
  workspace: StepWorkspace;
  /** The step that is open. */
  step: BarStep;
  /** The printed label of the open page. */
  pageLabel: string;
  /** The open page, or undefined when the book has none. */
  pageId: string | undefined;
  /** The open page and what it and the parts of the pages have for the steps, or undefined when the book has no page. */
  values: PageValues | undefined;
  /** The pages selected in the grid, which a shape set by hand can be carried over to. */
  selected?: ReadonlySet<string>;
  /** The page editor of the step on the open page, or null when the step has none or the page passes the step by. */
  editor: EditorSession | null;
}): React.JSX.Element {
  const { catalogue } = processing;
  const [overwrite, setOverwrite] = useState(false);
  const draft = processing.steps.find((entry) => entry.stepId === step.stepId);
  const issues = draft === undefined ? [] : (processing.orderIssues.get(draft.id) ?? []);
  const issueKind = issues.some((issue) => issue.kind === 'required') ? 'required' : 'usual';
  const processor = catalogue.find((entry) => entry.key === step.processorKey);
  const { page } = workspace;
  const state = page?.state ?? null;
  const found =
    page?.version === null || page?.version === undefined ? null : readResult(page.version);

  return (
    <section
      className="grid grid-cols-1 gap-4 border-b pb-4"
      aria-label={labels.label(step.number, step.title)}
      data-testid="step-panel"
      data-step-id={step.stepId}
    >
      <h3 className="text-base font-semibold" data-testid="step-panel-title">
        {MESSAGES.workspace.steps.step(step.number, step.title)}
      </h3>
      {step.enabled ? null : <p className="text-xs text-muted-foreground">{labels.off}</p>}
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

      <div className="grid grid-cols-1 gap-3" data-testid="step-panel-settings">
        <Heading>{labels.settings}</Heading>
        {draft === undefined || processor === undefined ? null : (
          <ParamsForm
            processor={processor}
            params={draft.params}
            idPrefix="step-panel"
            values={values === undefined ? undefined : { page: values, stepId: step.stepId }}
            onChange={(params) => processing.change(draft.id, params)}
          />
        )}
        {draft !== undefined && isPlacement(step.processorKey) ? (
          <MeasureBook processing={processing} step={draft} />
        ) : null}
      </div>

      <div className="grid gap-2" data-testid="step-panel-page">
        <Heading>{labels.thisPage(pageLabel)}</Heading>
        {found === null || found.skipped || found.angle === null ? null : (
          <p className="flex justify-between gap-4 text-sm" data-testid="step-panel-angle">
            <span className="text-muted-foreground">{MESSAGES.processing.thisPage.angle}</span>
            <span className="font-medium">{MESSAGES.processing.thisPage.degrees(found.angle)}</span>
          </p>
        )}
        {page !== null && page.version === null && page.input_version === null ? (
          <p className="text-xs text-muted-foreground" data-testid="step-panel-not-reached">
            {labels.notReached}
          </p>
        ) : null}
        {editor === null ? null : <EditorControls session={editor} />}
        {state === 'by-hand' && pageId !== undefined ? (
          <div className="grid gap-1" data-testid="step-carry">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">{labels.carry.hint}</span>
              <CarryOver
                processing={processing}
                pageId={pageId}
                stepId={step.stepId}
                title={labels.carry.title}
                selected={selected}
                overwrite={overwrite}
              />
            </div>
            <CheckboxField
              label={stepLabels.carry.overwrite}
              checked={overwrite}
              data-testid="step-carry-overwrite"
              onChange={(event) => setOverwrite(event.target.checked)}
            />
          </div>
        ) : null}
      </div>
    </section>
  );
}
