import type { PageVersionSchema } from '@/api';

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

/** Find the version of the step with a processor, and the version it read. */
export function stepVersions(
  chain: readonly PageVersionSchema[],
  processorKey: string,
): StepVersions {
  const index = chain.findIndex((version) => version.processor.key === processorKey);
  return {
    made: chain[index] ?? null,
    read: index > 0 ? (chain[index - 1] ?? null) : null,
  };
}
