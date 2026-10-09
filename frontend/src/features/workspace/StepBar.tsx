import type { FigureState } from '@/api';
import type { BarStep, StepStates } from '@/features/workspace/steps';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The bar of the steps of a stage, the row above the canvas: the button of the strip of pages at its start, the set of
 * steps shown, then the steps in the order they run, each with its number, its title and a dot for the state of its
 * shape on the open page.
 *
 * After the steps stand the actions the caller gives it, which are the catalogue that adds a step and the gear that opens
 * the list of the steps, and the button of the panel of the stage ends the bar. The open step is underlined. The state is never only the colour of the dot: the dot
 * has a word that a screen reader reads, and a step that is switched off says so. When the steps do not fit the row, the
 * bar scrolls and the steps that are not open keep their number.
 */

const labels = MESSAGES.workspace.steps;

const DOT: Readonly<Record<FigureState | 'unknown', string>> = {
  default: 'bg-muted-foreground/60',
  found: 'bg-status-done',
  'by-hand': 'bg-status-attention',
  skipped: 'bg-muted-foreground/25',
  unknown: 'bg-transparent ring-1 ring-border',
};

function Step({
  step,
  open,
  state,
  onOpen,
}: {
  step: BarStep;
  open: boolean;
  /** The state of the shape on the open page, or null while it is read. */
  state: FigureState | null;
  onOpen: () => void;
}): React.JSX.Element {
  const stateText = state === null ? labels.reading : labels.state[state];
  return (
    <li className="shrink-0">
      <button
        type="button"
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md border-b-2 border-transparent px-2 text-sm outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50',
          open && 'border-primary bg-accent font-medium',
          !step.enabled && 'text-muted-foreground',
        )}
        aria-current={open ? 'step' : undefined}
        aria-label={labels.open(step.number, step.title)}
        title={`${step.title} · ${stateText}`}
        data-testid="bar-step"
        data-step-id={step.stepId}
        data-state={state ?? 'unknown'}
        data-open={open}
        onClick={onOpen}
      >
        <span className="text-xs text-muted-foreground tabular-nums" aria-hidden="true">
          {step.number}
        </span>
        <span className={cn(open ? '' : 'max-lg:hidden', !step.enabled && 'line-through')}>
          {step.title}
        </span>
        <span
          className={cn('size-2 shrink-0 rounded-full', DOT[state ?? 'unknown'])}
          aria-hidden="true"
          data-testid="bar-step-dot"
        />
        <span className="sr-only">
          {stateText}
          {step.enabled ? '' : `. ${labels.off}`}
        </span>
      </button>
    </li>
  );
}

export function StepBar({
  steps,
  openId,
  states,
  recipePicker,
  onOpen,
  actions,
  leading,
  trailing,
}: {
  steps: readonly BarStep[];
  /** The identifier of the step that is open, or undefined when none is. */
  openId: string | undefined;
  states: StepStates;
  /** The choice between the sets of steps of the stage, which stands before the steps. */
  recipePicker?: React.ReactNode;
  /** Open a step. */
  onOpen: (stepId: string) => void;
  /** What stands after the steps: the button of the catalogue and the gear. */
  actions?: React.ReactNode;
  /** The button at the very start of the bar, which shows and hides the strip of pages. */
  leading?: React.ReactNode;
  /** The button at the very end of the bar, which shows and hides the panel of the stage. */
  trailing?: React.ReactNode;
}): React.JSX.Element {
  return (
    <nav
      className="flex h-10 shrink-0 items-center gap-2 border-b px-2"
      aria-label={labels.label}
      data-testid="step-bar"
    >
      {leading}
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
        {recipePicker}
        <ol className="flex items-center gap-1">
          {steps.map((step) => (
            <Step
              key={step.stepId}
              step={step}
              open={step.stepId === openId}
              state={states.get(step.stepId) ?? null}
              onOpen={() => onOpen(step.stepId)}
            />
          ))}
        </ol>
        {actions}
      </div>
      {trailing}
    </nav>
  );
}
