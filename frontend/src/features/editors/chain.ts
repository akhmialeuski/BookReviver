import type { PageVersionSchema, StepSchema } from '@/api';
import { versionOfStep } from '@/features/processing/stepRuns';

/**
 * The versions that made the current one of a stage on a page, one for each step of the recipe.
 *
 * Every step of a recipe stores a version that reads the one before, and the version the stage stands on is the last. An
 * editor lies on the picture its own step read and shows what its own step found, so it needs the version of its step and
 * the version before it.
 */

/** Follow the versions back from the current one, to the first step of the stage. */
export function stepChain(
  versions: readonly PageVersionSchema[],
  head: PageVersionSchema | null | undefined,
): PageVersionSchema[] {
  if (head === null || head === undefined) {
    return [];
  }
  const byId = new Map(versions.map((version) => [version.id, version]));
  const chain = [head];
  let cursor = head;
  while (cursor.input_id !== null) {
    const earlier = byId.get(cursor.input_id);
    if (earlier === undefined || earlier.stage !== head.stage || chain.includes(earlier)) {
      break;
    }
    chain.unshift(earlier);
    cursor = earlier;
  }
  return chain;
}

/** What an editor needs of the chain: the version of its step, and the version its step read. */
export interface StepVersions {
  /** The version the step made, or null when the page has none of it. */
  made: PageVersionSchema | null;
  /** The version before it in the stage, or null when the step is the first and reads the picture before the stage. */
  read: PageVersionSchema | null;
}

/**
 * Find the version of a step of the recipe, and the version it read.
 *
 * A recipe may run one processor twice, so the step is found by its place in the recipe and not by its processor: the
 * chain holds one version for each step that is on, and a version that is not of the processor of the step is the
 * version of a step the recipe has since changed, which this step has not made yet.
 *
 * @param chain The versions of the stage that made the current one, the first step first.
 * @param steps The steps of the recipe.
 * @param index Index of the step in the recipe.
 */
export function stepVersions(
  chain: readonly PageVersionSchema[],
  steps: readonly Pick<StepSchema, 'enabled' | 'processor_key'>[],
  index: number,
): StepVersions {
  const made = versionOfStep(chain, steps, index);
  if (made === null || made.processor.key !== steps[index]?.processor_key) {
    return { made: null, read: null };
  }
  const place = chain.indexOf(made);
  return { made, read: place > 0 ? (chain[place - 1] ?? null) : null };
}
