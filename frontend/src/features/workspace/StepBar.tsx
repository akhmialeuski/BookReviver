import type { FigureState, RecipeSchema } from '@/api';
import { type BarStep, markOfCondition, type StepStates } from '@/features/workspace/steps';
import { cn } from '@/shared/lib/utils';
import { MESSAGES } from '@/shared/messages';

/**
 * The bar of the steps of a stage, a row under the row above the canvas: the set of steps shown, then the steps in the
 * order they run, each with its number, its title, a mark when it processes only some pages, and a dot for the state of
 * its shape on the open page.
 *
 * After the steps stand the actions the caller gives it, which are the catalogue that adds a step and the gear that opens
 * the list of the steps. The open step is underlined, and pressing it again closes it. The state is never only the colour of the dot: the dot
 * has a word that a screen reader reads, and a step that is switched off says so. When the steps do not fit the row, the
 * bar scrolls and the steps that are not open keep their number and mark.
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
  const mark = markOfCondition(step.appliesTo);
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
        title={`${labels.step(step.number, step.title)} · ${stateText}`}
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
        {mark === null ? null : (
          <span
            className="text-xs text-muted-foreground"
            title={MESSAGES.processing.steps.condition.options[step.appliesTo]}
            data-testid="bar-step-mark"
            data-mark={mark}
          >
            {labels.marks[mark]}
          </span>
        )}
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
  recipes,
  recipeId,
  onChooseRecipe,
  onOpen,
  actions,
}: {
  steps: readonly BarStep[];
  /** The identifier of the step that is open, or undefined when none is. */
  openId: string | undefined;
  states: StepStates;
  /** The sets of steps of the stage, which the reader chooses between. */
  recipes: readonly Pick<RecipeSchema, 'id' | 'name'>[];
  /** The set of steps shown. */
  recipeId: string | undefined;
  onChooseRecipe: (id: string) => void;
  /** Open a step, or close the open one with undefined. */
  onOpen: (stepId: string | undefined) => void;
  /** What stands after the steps: the button of the catalogue and the gear. */
  actions?: React.ReactNode;
}): React.JSX.Element {
  return (
    <nav
      className="flex h-10 shrink-0 items-center gap-2 overflow-x-auto border-b px-2"
      aria-label={labels.label}
      data-testid="step-bar"
    >
      {recipes.length > 1 ? (
        <select
          aria-label={labels.recipe}
          data-testid="step-bar-recipe"
          className="h-8 min-w-0 shrink-0 rounded-md border border-input bg-background px-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          value={recipeId ?? ''}
          onChange={(event) => onChooseRecipe(event.target.value)}
        >
          {recipes.map((recipe) => (
            <option key={recipe.id} value={recipe.id}>
              {recipe.name}
            </option>
          ))}
        </select>
      ) : null}
      <ol className="flex items-center gap-1">
        {steps.map((step) => (
          <Step
            key={step.stepId}
            step={step}
            open={step.stepId === openId}
            state={states.get(step.stepId) ?? null}
            onOpen={() => onOpen(step.stepId === openId ? undefined : step.stepId)}
          />
        ))}
      </ol>
      {actions}
    </nav>
  );
}
