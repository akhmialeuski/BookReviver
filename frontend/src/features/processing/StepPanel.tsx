import {
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  PlayIcon,
  TriangleAlertIcon,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import type { AppliesTo, FigureState } from '@/api';
import { EditorControls } from '@/features/editors/EditorControls';
import { isPlacement } from '@/features/editors/placement';
import type { EditorSession } from '@/features/editors/session';
import { CarryOver } from '@/features/processing/CarryOver';
import { MeasureBook } from '@/features/processing/MeasureBook';
import { PageStepSettings } from '@/features/processing/PageStepSettings';
import { ParamsForm } from '@/features/processing/ParamsForm';
import { pageValuesOf } from '@/features/processing/pageSettings';
import { usePageSettings } from '@/features/processing/queries';
import { CONDITIONS } from '@/features/processing/recipe';
import { readResult } from '@/features/processing/results';
import { StepReset } from '@/features/processing/StepReset';
import { pagesOfCondition, RunScope } from '@/features/processing/scope';
import { canRunThrough, passedPages } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import type { BarStep } from '@/features/workspace/steps';
import type { StripItem } from '@/features/workspace/strip';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';
import { CheckboxField } from '@/shared/ui/checkbox-field';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * The section of the panel for the step that is open, which is where a step of a stage with a bar is set and looked at:
 * its settings, where it stands in the order and what is wrong with that, what it did on the open page and what the
 * page changes for it, how the pages of the book stand at it and how many passed it, the buttons that run the recipe up to
 * it, and the way to the steps either side of it.
 *
 * It stands above the sections of the recipe and of the open page and takes none of them away. The settings are the
 * draft of the recipe, which the window of the gear and the save bar share, so a change made here is the change made
 * there. Under the buttons of "Auto" is the menu that resets the step to its defaults. The changes and the results of the
 * step on the open page are not listed here but in the history that ends the panel of the stage.
 */

const labels = MESSAGES.workspace.stepPanel;
const stepLabels = MESSAGES.processing.steps;

const DOT: Readonly<Record<FigureState, string>> = {
  default: 'bg-muted-foreground/60',
  found: 'bg-status-done',
  'by-hand': 'bg-status-attention',
  skipped: 'bg-muted-foreground/25',
};

const NO_PAGES: ReadonlySet<string> = new Set();

/** The scopes of a run that the buttons of "Auto" do not name: the pages selected in the grid and those that need a look. */
const OTHER_SCOPES: ReadonlySet<RunScope> = new Set([RunScope.Selected, RunScope.Attention]);

function Heading({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
      {children}
    </h3>
  );
}

function Move({
  step,
  direction,
  onOpen,
}: {
  step: BarStep;
  direction: 'previous' | 'next';
  onOpen: (stepId: string) => void;
}): React.JSX.Element {
  const Icon = direction === 'previous' ? ChevronLeftIcon : ChevronRightIcon;
  return (
    <Button
      variant="outline"
      size="sm"
      className={cn(direction === 'next' && 'ml-auto flex-row-reverse')}
      aria-label={labels.moveTo(step.number, step.title)}
      data-testid={`step-${direction}`}
      onClick={() => onOpen(step.stepId)}
    >
      <Icon />
      {MESSAGES.workspace.steps.step(step.number, step.title)}
    </Button>
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
  onOpen,
}: {
  processing: Processing;
  workspace: StepWorkspace;
  /** The step that is open. */
  step: BarStep;
  /** The printed label of the open page. */
  pageLabel: string;
  /** The open page, or undefined when the book has none. */
  pageId: string | undefined;
  /** Every page of the book with where it stands in the stage, which the pages of the condition are counted from. */
  items: readonly StripItem[];
  /** The pages selected in the grid, which a shape set by hand can be carried over to. */
  selected?: ReadonlySet<string>;
  /** The page editor of the step on the open page, or null when the step has none or the page passes the step by. */
  editor: EditorSession | null;
  /** The run of the stage, which the buttons of "Auto" ask for. */
  run: StageRun;
  /** Open another step. */
  onOpen: (stepId: string) => void;
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
  const { page, counts, neighbours } = workspace;
  const state = page?.state ?? null;
  const found =
    page?.version === null || page?.version === undefined ? null : readResult(page.version);
  const runnable = recipe !== undefined && canRunThrough(recipe.steps, step.index);
  const ofCondition = pagesOfCondition(items, draft?.appliesTo ?? step.appliesTo);
  const hasCondition = (draft?.appliesTo ?? step.appliesTo) !== 'all';

  return (
    <section
      className="grid gap-4 border-b pb-4"
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

      <div className="grid gap-3" data-testid="step-panel-settings">
        <Heading>{labels.settings}</Heading>
        {draft === undefined ? null : (
          <label
            className="grid gap-1 text-xs text-muted-foreground"
            title={stepLabels.condition.hint}
          >
            {stepLabels.condition.label(step.title)}
            <select
              aria-label={labels.condition(step.title)}
              data-testid="step-panel-condition"
              className="h-9 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              value={draft.appliesTo}
              onChange={(event) => processing.condition(draft.id, event.target.value as AppliesTo)}
            >
              {CONDITIONS.map((condition) => (
                <option key={condition} value={condition}>
                  {stepLabels.condition.options[condition]}
                </option>
              ))}
            </select>
          </label>
        )}
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
        <p className="flex items-center gap-2 text-sm" data-testid="step-panel-state">
          <span
            className={cn(
              'size-2 shrink-0 rounded-full',
              state === null ? 'ring-1 ring-border' : DOT[state],
            )}
            aria-hidden="true"
          />
          <span className="text-muted-foreground">{labels.shape}</span>
          <span className="font-medium">
            {state === null
              ? MESSAGES.workspace.steps.reading
              : MESSAGES.workspace.steps.state[state]}
          </span>
        </p>
        {found === null || found.skipped || found.angle === null ? null : (
          <p className="flex justify-between gap-4 text-sm" data-testid="step-panel-angle">
            <span className="text-muted-foreground">{MESSAGES.processing.thisPage.angle}</span>
            <span className="font-medium">{MESSAGES.processing.thisPage.degrees(found.angle)}</span>
          </p>
        )}
        {state === null ? null : (
          <p className="text-xs text-muted-foreground" data-testid="step-panel-hint">
            {MESSAGES.editors.figure.hint[state]}
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
            {stepLabels.passed(
              passedPages(stageRows, recipe.id, step.index),
              run.choices.find(({ scope }) => scope === RunScope.All)?.count ?? 0,
            )}
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

      <div className="grid gap-2">
        <Button
          variant="outline"
          disabled={!runnable || run.disabled || pageId === undefined}
          data-testid="step-auto-page"
          onClick={() => pageId !== undefined && run.startPages([pageId], step.index)}
        >
          <PlayIcon />
          {labels.autoPage}
        </Button>
        {hasCondition ? (
          <Button
            variant="outline"
            disabled={!runnable || run.disabled || ofCondition.length === 0}
            data-testid="step-auto-condition"
            onClick={() =>
              run.startPages(
                ofCondition.map((item) => item.page.id),
                step.index,
              )
            }
          >
            <PlayIcon />
            {labels.autoCondition(ofCondition.length)}
          </Button>
        ) : null}
        <Button
          disabled={!runnable || run.disabled}
          title={labels.autoHint}
          data-testid="step-auto"
          onClick={() => run.start(RunScope.All, step.index)}
        >
          <PlayIcon />
          {labels.auto}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              disabled={!runnable || run.disabled}
              data-testid="step-auto-more"
            >
              <PlayIcon />
              {labels.autoMore}
              <ChevronDownIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>{stepLabels.runThrough}</DropdownMenuLabel>
            {run.choices
              .filter(({ scope }) => OTHER_SCOPES.has(scope))
              .map(({ scope, count }) => (
                <DropdownMenuItem
                  key={scope}
                  disabled={count === 0}
                  data-testid={`step-run-${scope}`}
                  onSelect={() => run.start(scope, step.index)}
                >
                  {run.describe(scope, count)}
                </DropdownMenuItem>
              ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <p className="text-xs text-muted-foreground">
          {run.dirty ? labels.saveFirst : labels.autoHint}
        </p>
      </div>

      <StepReset processing={processing} pageId={pageId} stepId={step.stepId} title={step.title} />

      <nav className="flex gap-2" aria-label={labels.moves}>
        {neighbours.previous === null ? null : (
          <Move step={neighbours.previous} direction="previous" onOpen={onOpen} />
        )}
        {neighbours.next === null ? null : (
          <Move step={neighbours.next} direction="next" onOpen={onOpen} />
        )}
      </nav>
    </section>
  );
}
