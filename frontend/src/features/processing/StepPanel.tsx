import { ChevronLeftIcon, ChevronRightIcon, PlayIcon, XIcon } from 'lucide-react';
import type { AppliesTo, FigureState } from '@/api';
import { ParamsForm } from '@/features/processing/ParamsForm';
import { CONDITIONS } from '@/features/processing/recipe';
import { readResult } from '@/features/processing/results';
import { RunScope } from '@/features/processing/scope';
import { canRunThrough } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StageRun } from '@/features/processing/useStageRun';
import type { BarStep } from '@/features/workspace/steps';
import type { StepWorkspace } from '@/features/workspace/useStepWorkspace';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';
import { Button } from '@/shared/ui/button';

/**
 * The section of the panel for the step that is open: its settings, what it did on the open page, how the pages of the
 * book stand at it, the button that runs the recipe up to it on every page, and the way to the steps either side of it.
 *
 * It stands above the sections of the recipe and of the open page and takes none of them away. The settings are the
 * draft of the step the recipe section edits, so a change made here is the change made there.
 */

const labels = MESSAGES.workspace.stepPanel;
const stepLabels = MESSAGES.processing.steps;

const DOT: Readonly<Record<FigureState, string>> = {
  default: 'bg-muted-foreground/60',
  found: 'bg-status-done',
  'by-hand': 'bg-status-attention',
  skipped: 'bg-muted-foreground/25',
};

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
  run,
  onOpen,
  onClose,
}: {
  processing: Processing;
  workspace: StepWorkspace;
  /** The step that is open. */
  step: BarStep;
  /** The printed label of the open page. */
  pageLabel: string;
  /** The run of the stage, which "Auto on all pages" asks for. */
  run: StageRun;
  /** Open another step. */
  onOpen: (stepId: string) => void;
  onClose: () => void;
}): React.JSX.Element {
  const { catalogue, recipe } = processing;
  const draft = processing.steps.find((entry) => entry.stepId === step.stepId);
  const processor = catalogue.find((entry) => entry.key === step.processorKey);
  const { page, counts, neighbours } = workspace;
  const state = page?.state ?? null;
  const found =
    page?.version === null || page?.version === undefined ? null : readResult(page.version);
  const runnable = recipe !== undefined && canRunThrough(recipe.steps, step.index);

  return (
    <section
      className="grid gap-4 border-b pb-4"
      aria-label={labels.label(step.number, step.title)}
      data-testid="step-panel"
      data-step-id={step.stepId}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold" data-testid="step-panel-title">
          {MESSAGES.workspace.steps.step(step.number, step.title)}
        </h3>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={labels.close}
          title={labels.close}
          data-testid="step-close"
          onClick={onClose}
        >
          <XIcon />
        </Button>
      </div>
      {step.enabled ? null : <p className="text-xs text-muted-foreground">{labels.off}</p>}

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
            onChange={(params) => processing.change(draft.id, params)}
          />
        )}
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
        {page !== null && page.version === null && page.input_version === null ? (
          <p className="text-xs text-muted-foreground" data-testid="step-panel-not-reached">
            {labels.notReached}
          </p>
        ) : null}
      </div>

      <div className="grid gap-2" data-testid="step-panel-book">
        <Heading>{labels.book}</Heading>
        {counts === null ? (
          <p className="text-sm text-muted-foreground">{MESSAGES.workspace.steps.reading}</p>
        ) : (
          <dl className="grid gap-1 text-sm">
            {(
              [
                ['found', counts.found],
                ['byHand', counts.byHand],
                ['check', counts.check],
                ['skipped', counts.skipped],
                ['notRun', counts.notRun],
              ] as const
            ).map(([key, value]) => (
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
          disabled={!runnable || run.disabled}
          title={labels.autoHint}
          data-testid="step-auto"
          onClick={() => run.start(RunScope.All, step.index)}
        >
          <PlayIcon />
          {labels.auto}
        </Button>
        <p className="text-xs text-muted-foreground">
          {run.dirty ? labels.saveFirst : labels.autoHint}
        </p>
      </div>

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
