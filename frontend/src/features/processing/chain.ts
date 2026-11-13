import type { PageVersionSchema, StepSchema } from '@/api';
import { versionOfStep } from '@/features/processing/stepRuns';

/**
 * The versions that made the current one of a stage on a page, one for each step of the recipe.
 *
 * Every step of a recipe stores a version that reads the one before, and the version the stage stands on is the last. An
 * editor shows what its own step found, so it needs the version of its step.
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

/**
 * Find the version a step of the recipe made.
 *
 * A recipe may run one processor twice, so the step is found by its place in the recipe and not by its processor: the
 * chain holds one version for each step that is on, and a version that is not of the processor of the step is the
 * version of a step the recipe has since changed, which this step has not made yet.
 *
 * @param chain The versions of the stage that made the current one, the first step first.
 * @param steps The steps of the recipe.
 * @param index Index of the step in the recipe.
 * @returns The version the step made, or null when the page has none of it.
 */
export function stepVersion(
  chain: readonly PageVersionSchema[],
  steps: readonly Pick<StepSchema, 'enabled' | 'processor_key'>[],
  index: number,
): PageVersionSchema | null {
  const made = versionOfStep(chain, steps, index);
  return made !== null && made.processor.key === steps[index]?.processor_key ? made : null;
}
