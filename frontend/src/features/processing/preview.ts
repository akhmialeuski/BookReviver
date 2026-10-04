import type { PageVersionSchema, StepBody } from '@/api';
import { isPlacement } from '@/features/editors/placement';

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

/**
 * The settings of the Margins step that a page size or a line height of 0 leaves to the book, so a preview records the
 * value the book gave and not the 0 the ask names.
 */
const BOOK_FIELDS: ReadonlySet<string> = new Set(['page_width', 'page_height', 'line_height']);

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
  // A page that did not meet the condition of the step passed it as it was, so its version holds no parameters
  if (version.data.skipped_by_condition === true) {
    return true;
  }
  const asked = last.params ?? {};
  const byTheBook = isPlacement(last.processor_key);
  return Object.entries(asked).every(
    ([name, value]) =>
      (byTheBook && BOOK_FIELDS.has(name) && value === 0) ||
      JSON.stringify(canonical(version.params[name])) === JSON.stringify(canonical(value)),
  );
}
