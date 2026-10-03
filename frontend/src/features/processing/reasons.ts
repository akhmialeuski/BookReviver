import type { ProcessorSchema, RecipeSchema } from '@/api';
import { readResult } from '@/features/processing/results';
import { stepOfProcessor } from '@/features/processing/stepRuns';
import { needsCheck, type StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * The words under a page of the strip that asks for a look, which say why: it failed, it is out of date, or the step
 * finished and was not sure, and which step it was when the recipe has several.
 *
 * A page that asks for nothing has no reason, and the strip draws nothing under it.
 *
 * @param item A page of the strip with its row in the stage.
 * @returns The reason, or null for a page that needs no look.
 */
export function reasonOf(item: StripItem): string | null {
  const { row } = item;
  if (row === undefined || !needsCheck(item)) {
    return null;
  }
  const words = MESSAGES.processing.reasons;
  if (row.status === 'failed') {
    return words.failed(row.version?.error ?? '');
  }
  if (row.status === 'stale') {
    return words.stale;
  }
  const confidence = row.version === null ? null : readResult(row.version).confidence;
  switch (row.review) {
    case 'not-applied':
      return words.notApplied(confidence);
    case 'unsure-gutter':
      return words.unsureGutter(confidence);
    case 'narrow-gutter':
      return words.narrowGutter;
    case 'cut-by-edge':
      return words.cutByEdge;
    case 'size-differs':
      return words.sizeDiffers;
    case 'few-lines':
      return words.fewLines;
    case 'high-residual':
      return words.highResidual;
    default:
      return words.lowConfidence(confidence);
  }
}

/**
 * Make the function that writes the reason under a page of the Check filter, naming the step that marked the page.
 *
 * Only a page whose result is up to date and marked is named after a step. A page that failed or is out of date says that
 * and nothing more, as the step that marked it is not what the reader needs to act on.
 *
 * @param recipes Every recipe of the stage, in which the step is looked for.
 * @param catalogue The processors of the stage, which give the step its title.
 * @returns The function that writes the reason of a page, or null for a page that needs no look.
 */
export function reasonWithStep(
  recipes: readonly Pick<RecipeSchema, 'id' | 'steps'>[],
  catalogue: readonly Pick<ProcessorSchema, 'key' | 'title'>[],
): (item: StripItem) => string | null {
  return (item) => {
    const reason = reasonOf(item);
    const { row } = item;
    if (reason === null || row === undefined || row.status !== 'fresh') {
      return reason;
    }
    if (row.review === null || row.review_processor === null) {
      return reason;
    }
    const recipe = recipes.find((entry) => entry.id === row.recipe_id);
    const index = stepOfProcessor(recipe, row.review_processor);
    // A recipe of one step has no other step to tell it from
    if (index === null || (recipe?.steps.length ?? 0) < 2) {
      return reason;
    }
    const title =
      catalogue.find((processor) => processor.key === row.review_processor)?.title ??
      row.review_processor;
    return MESSAGES.processing.reasons.atStep(index + 1, title, reason);
  };
}
