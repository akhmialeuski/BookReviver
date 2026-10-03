import type { PageVersionSchema, RecipeSchema, StagePageSchema, StepSchema } from '@/api';

/**
 * What a run of a stage up to one step leaves on the pages, as the panel reads it: how many pages passed each step, which
 * step of the recipe a processor is, and which version of a page is the result of a step.
 *
 * A step is named by its index in the recipe, from zero, as the server names it, and shown by its number from one. A page
 * that was run through every step that is on has no stopping step, so it passed every step.
 */

/** Tell whether the row of a page holds a result of the stage that was made, whether or not it is out of date now. */
function hasResult(row: StagePageSchema): boolean {
  return row.status === 'fresh' || row.status === 'stale';
}

/**
 * Count the pages whose result was made through a step or a later one.
 *
 * @param rows The rows of the stage.
 * @param recipeId The recipe the steps belong to; a page processed by another recipe passed none of them.
 * @param stepIndex Index of the step in the recipe.
 */
export function passedPages(
  rows: readonly StagePageSchema[],
  recipeId: string,
  stepIndex: number,
): number {
  return rows.filter(
    (row) =>
      hasResult(row) &&
      row.recipe_id === recipeId &&
      (row.through_step === null || row.through_step >= stepIndex),
  ).length;
}

/** Tell whether a run can go up to a step: it, or a step before it, is switched on. */
export function canRunThrough(
  steps: readonly Pick<StepSchema, 'enabled'>[],
  index: number,
): boolean {
  return steps.slice(0, index + 1).some((step) => step.enabled);
}

/** Find the index of the first step of a recipe that runs a processor, or null for a recipe that has none such. */
export function stepOfProcessor(
  recipe: Pick<RecipeSchema, 'steps'> | undefined,
  processorKey: string,
): number | null {
  const found = recipe?.steps.findIndex((step) => step.processor_key === processorKey) ?? -1;
  return found < 0 ? null : found;
}

/**
 * Find the version a step of the recipe made on a page, in the chain of versions that made the current one.
 *
 * The chain holds one version for each step that is on, in order, so the place of a step in it is the number of steps
 * that are on before it.
 *
 * @param chain The versions of the stage that made the current one, the first step first.
 * @param steps The steps of the recipe.
 * @param index Index of the step in the recipe.
 * @returns The version, or null for a step that is off, or one the page has not reached.
 */
export function versionOfStep(
  chain: readonly PageVersionSchema[],
  steps: readonly Pick<StepSchema, 'enabled'>[],
  index: number,
): PageVersionSchema | null {
  if (steps[index]?.enabled !== true) {
    return null;
  }
  const place = steps.slice(0, index).filter((step) => step.enabled).length;
  return chain[place] ?? null;
}
