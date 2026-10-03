import type {
  AppliesTo,
  FigureState,
  ProcessorSchema,
  RecipeSchema,
  Stage,
  StagePageSchema,
} from '@/api';

/**
 * The steps of the open recipe as the step bar and the step panel read them: the saved steps with their titles, the step
 * the address names, the steps on either side of it, and what a step did on the pages of the book.
 *
 * The bar shows the saved recipe and not the draft, since a step is addressed by the identifier the server gave it, and a
 * step that was only added to the draft has none yet.
 */

/** The stages whose steps have a bar and a workspace of their own. */
const STAGES_WITH_STEP_BAR: ReadonlySet<Stage> = new Set<Stage>(['geometry']);

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
  /** Which pages the step processes. */
  appliesTo: AppliesTo;
}

/** The marks a condition has on the bar, in the order the select of a step lists the conditions. */
export const ConditionMark = {
  Text: 'text',
  Picture: 'picture',
} as const;

/** One mark (derived from {@link ConditionMark}). */
export type ConditionMark = (typeof ConditionMark)[keyof typeof ConditionMark];

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
    appliesTo: step.applies_to,
  }));
}

/** Give the mark a condition carries on the bar, or null for a step that processes every page. */
export function markOfCondition(appliesTo: AppliesTo): ConditionMark | null {
  switch (appliesTo) {
    case 'all':
      return null;
    case 'text':
      return ConditionMark.Text;
    case 'pictures':
    case 'color-pictures':
    case 'bw-pictures':
      return ConditionMark.Picture;
  }
}

/** Find the step an address names among the steps of the recipe, or null when the recipe has none such. */
export function openStepOf(steps: readonly BarStep[], stepId: string | undefined): BarStep | null {
  return steps.find((step) => step.stepId === stepId) ?? null;
}

/** The steps either side of a step, which are the ones the panel offers to move to. */
export interface Neighbours {
  previous: BarStep | null;
  next: BarStep | null;
}

/** Find the step before and the step after one, which count steps that are off too, since the bar lists them all. */
export function neighboursOf(steps: readonly BarStep[], open: BarStep): Neighbours {
  return { previous: steps[open.index - 1] ?? null, next: steps[open.index + 1] ?? null };
}

/** How the pages of the book stand at one step. */
export interface StepCounts {
  /** Pages whose shape the step found. */
  found: number;
  /** Pages whose shape the reader set by hand. */
  byHand: number;
  /** Pages the step passes by its condition. */
  skipped: number;
  /** Pages the step has not run on and that hold the default shape. */
  notRun: number;
  /** Pages the step asked a second look at, which is the mark of this very processor. */
  check: number;
}

/**
 * Count the pages of the book by what the step did on them.
 *
 * @param rows The rows of the stage asked for the step.
 * @param processorKey The key of the processor of the step, whose review marks are counted as the pages to check.
 */
export function countStep(rows: readonly StagePageSchema[], processorKey: string): StepCounts {
  const counts: StepCounts = { found: 0, byHand: 0, skipped: 0, notRun: 0, check: 0 };
  for (const row of rows) {
    const state = row.step?.state;
    if (state === undefined) {
      continue;
    }
    switch (state) {
      case 'found':
        counts.found += 1;
        break;
      case 'by-hand':
        counts.byHand += 1;
        break;
      case 'skipped':
        counts.skipped += 1;
        break;
      case 'default':
        counts.notRun += 1;
        break;
    }
    if (state !== 'skipped' && row.review !== null && row.review_processor === processorKey) {
      counts.check += 1;
    }
  }
  return counts;
}

/** The state of the shape of each step on the open page, by the identifier of the step; null while it is read. */
export type StepStates = ReadonlyMap<string, FigureState | null>;
