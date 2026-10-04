import type { PageStepSettingsSchema } from '@/api';

/**
 * What the open page changes for the steps of a recipe: the fields it has its own value for, read from the settings the
 * server lists, and the values the settings form starts from.
 */

const NO_VALUES: Readonly<Record<string, unknown>> = {};

/**
 * Find the fields a page changes for one step.
 *
 * @param settings The settings of the page in the stage, one entry for each step that has any.
 * @param stepId The identifier of the saved step, or null for a step that is not saved yet and so has none.
 * @returns The value of each field the page changes, by name; empty when it changes none.
 */
export function pageValuesOf(
  settings: readonly PageStepSettingsSchema[] | undefined,
  stepId: string | null,
): Readonly<Record<string, unknown>> {
  if (stepId === null) {
    return NO_VALUES;
  }
  return settings?.find((entry) => entry.step_id === stepId)?.params ?? NO_VALUES;
}

/**
 * Lay the values of a page over the parameters of a step, which is what the step runs with on that page.
 *
 * @param params The parameters of the step in the recipe.
 * @param pageValues The fields the page changes.
 */
export function effectiveParams(
  params: Readonly<Record<string, unknown>>,
  pageValues: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  return { ...params, ...pageValues };
}

/**
 * Find the fields a form changed, by comparing it with the values it started from.
 *
 * @param before The values the form started from.
 * @param after The values the form holds now.
 * @returns The name and the new value of each field that is not what it was.
 */
export function changedFields(
  before: Readonly<Record<string, unknown>>,
  after: Readonly<Record<string, unknown>>,
): [string, unknown][] {
  return Object.entries(after).filter(
    ([name, value]) => JSON.stringify(value) !== JSON.stringify(before[name]),
  );
}

/** Write the value of a field as a sentence quotes it: a text as it is, anything else as JSON. */
export function showValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}
