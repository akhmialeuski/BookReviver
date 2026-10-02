import type { PageVersionSchema, StepBody } from '@/api';

/**
 * What a preview asks for, how two asks are told apart, and whether a version the server announced is the answer to
 * one of them.
 *
 * A preview is a version of the preview scale that is made in the background and announced by an event with only its
 * identifier, so the identifier is read and the version is checked against the ask before it is shown: an event may be
 * about another page, or about an earlier ask the reader has already left behind.
 */

/** One preview: the steps of the form, run on a page up to one of them. */
export interface PreviewRequest {
  pageId: string;
  steps: readonly StepBody[];
  /** The index of the last step whose result is wanted. */
  stepIndex: number;
}

/** How long a change of the form waits for another before a preview is asked for. */
export const PREVIEW_DELAY_MS = 400;

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(canonical);
  }
  if (typeof value === 'object' && value !== null) {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entry]) => entry !== undefined)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, entry]) => [key, canonical(entry)]),
    );
  }
  return value;
}

/**
 * Give a text that is the same for two asks that mean the same thing, whatever the order of the keys of their
 * parameters, so asking again for what is shown starts no work.
 */
export function previewKey(request: PreviewRequest): string {
  return JSON.stringify(
    canonical({
      page: request.pageId,
      index: request.stepIndex,
      // A step that is off is left out of a preview, so it does not tell two asks apart
      steps: request.steps.slice(0, request.stepIndex + 1).filter((step) => step.enabled !== false),
    }),
  );
}

/** Give the processor of the last step a preview runs, which is the step its version belongs to. */
function lastStepOf(request: PreviewRequest): StepBody | undefined {
  return request.steps.slice(0, request.stepIndex + 1).findLast((step) => step.enabled !== false);
}

/**
 * Tell whether a version is the result of a preview ask.
 *
 * It must be a ready preview of the asked page, made by the processor of the last step that is on, with every
 * parameter the ask names equal to the one the version recorded. The version holds the parameters with the defaults of
 * the processor filled in, so the ask may name fewer.
 *
 * @param version A version the server announced.
 * @param request The ask.
 */
export function answersPreview(version: PageVersionSchema, request: PreviewRequest): boolean {
  const last = lastStepOf(request);
  if (
    last === undefined ||
    version.scale !== 'preview' ||
    version.state !== 'ready' ||
    version.preview === null ||
    version.page_id !== request.pageId ||
    version.processor.key !== last.processor_key
  ) {
    return false;
  }
  const asked = last.params ?? {};
  return Object.entries(asked).every(
    ([name, value]) =>
      JSON.stringify(canonical(version.params[name])) === JSON.stringify(canonical(value)),
  );
}
