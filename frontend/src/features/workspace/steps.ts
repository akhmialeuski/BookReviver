import type { FigureState, ProcessorSchema, RecipeSchema, Stage, StagePageSchema } from '@/api';

/**
 * The steps of the open recipe as the step bar and the step panel read them: the saved steps with their titles, the step
 * the address names, and what a step did on the pages of the book.
 *
 * The bar shows the saved recipe and not the draft, since a step is addressed by the identifier the server gave it, and a
 * step that was only added to the draft has none yet.
 */

/** The stages whose steps have a bar and a workspace of their own. */
const STAGES_WITH_STEP_BAR: ReadonlySet<Stage> = new Set<Stage>(['geometry', 'cleanup']);

/** Tell whether a stage shows the bar of its steps. */
export function hasStepBar(stage: Stage): boolean {
  return STAGES_WITH_STEP_BAR.has(stage);
}

/** One saved step of the recipe, as the bar lists it. */
export interface BarStep {
  /** The identifier of the step, which its address, its edits and its versions are kept by. */
  stepId: string;
  /** Its place in the recipe from one, which counts the steps that are off too, as the list of the panel does. */
  number: number;
  /** Its place in the recipe from zero, which a run up to the step is asked for by. */
  index: number;
  /** The title of its processor, or the key of the processor when it is not installed on this machine. */
  title: string;
  processorKey: string;
  enabled: boolean;
}

/**
 * List the saved steps of a recipe with the titles of their processors.
 *
 * @param recipe The recipe shown, or undefined while it is read.
 * @param catalogue The processors of the stage.
 */
export function barStepsOf(
  recipe: Pick<RecipeSchema, 'steps'> | undefined,
  catalogue: readonly Pick<ProcessorSchema, 'key' | 'title'>[],
): BarStep[] {
  return (recipe?.steps ?? []).map((step, index) => ({
    stepId: step.step_id,
    number: index + 1,
    index,
    title: catalogue.find((entry) => entry.key === step.processor_key)?.title ?? step.processor_key,
    processorKey: step.processor_key,
    enabled: step.enabled,
  }));
}

/** Find the step an address names among the steps of the recipe, or null when the recipe has none such. */
export function openStepOf(steps: readonly BarStep[], stepId: string | undefined): BarStep | null {
  return steps.find((step) => step.stepId === stepId) ?? null;
}

/**
 * Choose the step a stage with a bar opens on when its address names none: the furthest step of the recipe that a run
 * has brought a page of that recipe to.
 *
 * When some page of the recipe went through every step that is on, it is the last step that is on. Otherwise it is the
 * step the furthest run stopped at, and when no run has made a result of the recipe, the last step of the recipe.
 *
 * @param steps The saved steps of the recipe shown in the bar.
 * @param rows The rows of the stage, without a step.
 * @param recipeId The recipe shown in the bar.
 * @returns The step, or null when the recipe has no steps.
 */
export function defaultStepOf(
  steps: readonly BarStep[],
  rows: readonly StagePageSchema[],
  recipeId: string,
): BarStep | null {
  const last = steps[steps.length - 1] ?? null;
  const ran = rows.filter(
    (row) => row.recipe_id === recipeId && row.version !== null && row.status !== 'failed',
  );
  if (ran.length === 0) {
    return last;
  }
  if (ran.some((row) => row.through_step === null)) {
    return [...steps].reverse().find((step) => step.enabled) ?? last;
  }
  const furthest = Math.max(...ran.map((row) => row.through_step ?? 0));
  return steps[furthest] ?? last;
}

/** How the pages of the book stand at one step. */
export interface StepCounts {
  /** Pages whose shape the step found. */
  found: number;
  /** Pages with a setting of their own for the step or a shape the reader set by hand. */
  byHand: number;
  /** Pages the step passed unchanged, which are the leaves the program drew. */
  skipped: number;
  /** Pages the step has not run on and that hold the default shape. */
  notRun: number;
  /** Pages the step was unsure of. */
  check: number;
  /** Pages whose value at the step departs notably from the rest of the book. */
  unusual: number;
}

/**
 * Count the pages of the book by what the step did on them.
 *
 * The flags are the server's, which decides which page is unsure, unusual or set by hand, so the counts and the filters
 * of the strip never disagree.
 *
 * @param rows The rows of the stage asked for the step.
 */
export function countStep(rows: readonly StagePageSchema[]): StepCounts {
  const counts: StepCounts = { found: 0, byHand: 0, skipped: 0, notRun: 0, check: 0, unusual: 0 };
  for (const row of rows) {
    if (row.step === null) {
      continue;
    }
    switch (row.step.state) {
      case 'found':
        counts.found += 1;
        break;
      case 'skipped':
        counts.skipped += 1;
        break;
      case 'default':
        counts.notRun += 1;
        break;
    }
    counts.byHand += row.step.flags.includes('by-hand') ? 1 : 0;
    counts.check += row.step.flags.includes('unsure') ? 1 : 0;
    counts.unusual += row.step.flags.includes('unusual') ? 1 : 0;
  }
  return counts;
}

/** The state of the shape of each step on the open page, by the identifier of the step; null while it is read. */
export type StepStates = ReadonlyMap<string, FigureState | null>;
