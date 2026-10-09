import type { QueryClient } from '@tanstack/react-query';
import { redirect } from '@tanstack/react-router';
import { processorsOptions, recipesOptions } from '@/features/processing/queries';
import { shownRecipe } from '@/features/processing/useProcessing';
import { parseStage } from '@/features/stages/parse';
import { stageRowsOptions } from '@/features/workspace/queries';
import { barStepsOf, defaultStepOf, hasStepBar } from '@/features/workspace/steps';

/**
 * Sends the address of a stage with a step bar that names no step to the step the stage opens on.
 *
 * The router calls it as the loader of the address, so the choice belongs to the navigation that is under way: a later
 * navigation cancels the loader, and the redirect it would have made never overwrites the address that was asked for. The
 * data is read through the options the screen reads it by, so the screen finds it in the cache.
 */

/**
 * Read what the choice depends on and, for a stage with a bar, throw the redirect to the step the stage opens on.
 *
 * The screen shows the recipe of the kind of the open page, which is the page the address names or else the first page,
 * so the choice is made on that recipe and on the rows of the stage as the screen would make it. A stage with no bar, no
 * installed processor or a recipe with no steps stays where it is.
 *
 * @param queryClient The client whose cache the screens read, which the requests fill.
 * @param params The book and the stage of the address, as the router parsed them.
 * @param pageId The page the address opens, or undefined for the first page of the stage.
 * @throws A redirect to the step, in place of the address, which keeps the search params.
 */
export async function redirectToDefaultStep(
  queryClient: QueryClient,
  params: { projectId: string; stage: string },
  pageId: string | undefined,
): Promise<void> {
  const stage = parseStage(params.stage);
  if (stage === null || !hasStepBar(stage)) {
    return;
  }
  const processors = await queryClient.ensureQueryData(processorsOptions());
  // A stage no installed processor builds shows no recipe
  if (!processors.items.some((processor) => processor.stage === stage)) {
    return;
  }
  const [recipes, rows] = await Promise.all([
    queryClient.ensureQueryData(recipesOptions(params.projectId, stage)),
    queryClient.ensureQueryData(stageRowsOptions(params.projectId, stage)),
  ]);
  // The panel shows the recipe of the kind of the open page, the page the address names or else the first one
  const open = rows.find((row) => row.page_id === pageId) ?? rows[0];
  const recipe = shownRecipe(recipes.items, stage, undefined, open?.kind, null);
  if (recipe === undefined) {
    return;
  }
  // The titles are not read, so the steps need no catalogue
  const step = defaultStepOf(barStepsOf(recipe, []), rows, recipe.id);
  if (step !== null) {
    throw redirect({
      to: '/projects/$projectId/stages/$stage/steps/$stepId',
      params: { projectId: params.projectId, stage: params.stage, stepId: step.stepId },
      search: true,
      replace: true,
    });
  }
}
