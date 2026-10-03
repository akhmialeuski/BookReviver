import { useMemo } from 'react';
import type { PageVersionSchema, RecipeSchema } from '@/api';
import { stepChain } from '@/features/editors/chain';
import { useVersions } from '@/features/processing/queries';
import { versionOfStep } from '@/features/processing/stepRuns';
import type { Processing } from '@/features/processing/useProcessing';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The result of the step the reader chose to look at on the open page.
 *
 * The stage stands on the last step a page was run through, so by default there is nothing to choose and the canvas and
 * "This page" show that. When a step is chosen, its version is found in the chain of versions that made the current one,
 * and a page that has not reached the step has none, which the callers say.
 */

/** What is shown of a step on the open page. */
export interface ShownStep {
  /** The recipe the page was processed by, which the steps are counted in, or undefined while it is not known. */
  recipe: RecipeSchema | undefined;
  /** The versions that made the current one, the first step first. */
  chain: readonly PageVersionSchema[];
  /** The index of the step chosen, or null for the result the stage stands on. */
  index: number | null;
  /** The version of the step chosen, or null for no step chosen or one the page has not reached. */
  version: PageVersionSchema | null;
  /** Whether the page has a result of the step chosen, which is so when no step is chosen. */
  reached: boolean;
}

/**
 * Read the step shown on a page.
 *
 * @param processing The panel state, which holds the step chosen.
 * @param item The open page with its row in the stage.
 */
export function useShownStep(processing: Processing, item: StripItem | undefined): ShownStep {
  const versions = useVersions(processing.projectId, item?.page.id, processing.stage);
  const head = item?.row?.version ?? null;
  const chain = useMemo(() => stepChain(versions.data ?? [], head), [versions.data, head]);
  // A page keeps the steps of the recipe it was processed by, which is not always the one shown in the panel
  const recipe =
    processing.recipes.find((entry) => entry.id === item?.row?.recipe_id) ?? processing.recipe;
  const index = processing.shownStep;
  const version =
    index === null || recipe === undefined ? null : versionOfStep(chain, recipe.steps, index);
  return { recipe, chain, index, version, reached: index === null || version !== null };
}
