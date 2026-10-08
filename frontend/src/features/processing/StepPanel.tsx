import { TriangleAlertIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { EditorControls } from '@/features/editors/EditorControls';
import { isPlacement } from '@/features/editors/placement';
import type { EditorSession } from '@/features/editors/session';
import { CarryOver } from '@/features/processing/CarryOver';
import { MeasureBook } from '@/features/processing/MeasureBook';
import { PageStepSettings } from '@/features/processing/PageStepSettings';
import { ParamsForm } from '@/features/processing/ParamsForm';
import { pageValuesOf } from '@/features/processing/pageSettings';
import { usePageSettings } from '@/features/processing/queries';
import { readResult } from '@/features/processing/results';
import { StepReset } from '@/features/processing/StepReset';
import { passedPages } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import type { BarStep } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';

/**
 * The section of the panel for the step that is open, which is where a step of a stage with a bar is set and looked at:
 * its settings, where it stands in the order and what is wrong with that, what it did on the open page and what the
 * page changes for it, and how the pages of the book stand at it and how many passed it. A run starts from the foot of the
 * panel and not from here.
 *
 * It stands above the sections of the recipe and of the open page and takes none of them away. The settings are the
 * draft of the recipe, which the window of the gear and the save bar share, so a change made here is the change made
 * there. At the end is the menu that resets the step to its defaults. The changes and the results of the
 * step on the open page are not listed here but in the history that ends the panel of the stage.
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
  items,
  selected = NO_PAGES,
  editor,
  run,
}: {
  processing: Processing;
  workspace: StepWorkspace;
  /** The step that is open. */
  step: BarStep;
  /** The printed label of the open page. */
  pageLabel: string;
  /** The open page, or undefined when the book has none. */
  pageId: string | undefined;
  /** Every page of the book with where it stands in the stage, which the pages of the kind of the step are counted from. */
  items: readonly StripItem[];
  /** The pages selected in the grid, which a shape set by hand can be carried over to. */
  selected?: ReadonlySet<string>;
  /** The page editor of the step on the open page, or null when the step has none or the page passes the step by. */
  editor: EditorSession | null;
  /** The run of the stage, which says how many pages the book has for the count of the pages that passed the step. */
  run: StageRun;
}): React.JSX.Element {
  const { catalogue, recipe } = processing;
  const [overwrite, setOverwrite] = useState(false);
  const pageSettings = usePageSettings(processing.projectId, pageId, processing.stage);
  const draft = processing.steps.find((entry) => entry.stepId === step.stepId);
  const pageValues = pageValuesOf(pageSettings.data, step.stepId);
  const marked = useMemo(() => new Set(Object.keys(pageValues)), [pageValues]);
  const issues = draft === undefined ? [] : (processing.orderIssues.get(draft.id) ?? []);
  const issueKind = issues.some((issue) => issue.kind === 'required') ? 'required' : 'usual';
  const stageRows = items.flatMap((item) => (item.row === undefined ? [] : [item.row]));
  const processor = catalogue.find((entry) => entry.key === step.processorKey);
  const { page, counts } = workspace;
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
            marked={marked}
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
              label={MESSAGES.processing.steps.pageSettings.carry.overwrite}
              checked={overwrite}
              data-testid="step-carry-overwrite"
              onChange={(event) => setOverwrite(event.target.checked)}
            />
          </div>
        ) : null}
        {draft === undefined || pageId === undefined ? null : (
          <PageStepSettings
            // The form of the page holds the values of one step, so another step starts from its own and not from these
            key={draft.id}
            processing={processing}
            step={draft}
            processor={processor}
            pageId={pageId}
            pageValues={pageValues}
            selected={selected}
          />
        )}
      </div>

      <div className="grid gap-2" data-testid="step-panel-book">
        <Heading>{labels.book}</Heading>
        {recipe === undefined || !step.enabled || recipe.steps.length < 2 ? null : (
          <p className="text-sm" title={stepLabels.passedHint} data-testid="step-passed">
            {stepLabels.passed(passedPages(stageRows, recipe.id, step.index), run.total)}
          </p>
        )}
        {counts === null ? (
          <p className="text-sm text-muted-foreground">{MESSAGES.workspace.steps.reading}</p>
        ) : (
          <dl className="grid gap-1 text-sm">
            {(
              [
                ['found', counts.found],
                ['byHand', counts.byHand],
                ['check', counts.check],
                ['unusual', counts.unusual],
                ['skipped', counts.skipped],
                ['notRun', counts.notRun],
              ] as const
            )
              // A step that finds nothing to compare with the book has no pages that differ, so the line is left out
              .filter(([key, value]) => key !== 'unusual' || value > 0)
              .map(([key, value]) => (
                <div
                  key={key}
                  className="flex justify-between gap-4"
                  data-testid={`step-count-${key}`}
                >
                  <dt className="text-muted-foreground">{labels.counts[key]}</dt>
                  <dd className="font-medium">{labels.pages(value)}</dd>
                </div>
              ))}
          </dl>
        )}
      </div>

      <StepReset processing={processing} pageId={pageId} stepId={step.stepId} title={step.title} />
    </section>
  );
}
