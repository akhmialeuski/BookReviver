import { useMemo } from 'react';
import type { PageVersionSchema, RecipeSchema } from '@/api';
import { stepChain } from '@/features/editors/chain';
import { useVersions } from '@/features/processing/queries';
import type { Processing } from '@/features/processing/useProcessing';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The versions that made the current result of the stage on the open page, and the recipe they were made by.
 *
 * The stage stands on the last step a page was run through, so "This page" reads what the steps found down this chain.
 */

/** What is known of how the open page came to its result. */
export interface PageChain {
  /** The recipe the page was processed by, which the steps are counted in, or undefined while it is not known. */
  recipe: RecipeSchema | undefined;
  /** The versions that made the current one, the first step first. */
  chain: readonly PageVersionSchema[];
}

/**
 * Read the chain of versions of a page.
 *
 * @param processing The panel state.
 * @param item The open page with its row in the stage.
 */
export function usePageChain(processing: Processing, item: StripItem | undefined): PageChain {
  const versions = useVersions(processing.projectId, item?.page.id, processing.stage);
  const head = item?.row?.version ?? null;
  const chain = useMemo(() => stepChain(versions.data ?? [], head), [versions.data, head]);
  // A page keeps the steps of the recipe it was processed by, which is not always the one shown in the panel
  const recipe =
    processing.recipes.find((entry) => entry.id === item?.row?.recipe_id) ?? processing.recipe;
  return { recipe, chain };
}
