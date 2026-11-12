/**
 * Who sets the margins of the normalize step, the measure of the book or the reader.
 *
 * The step keeps the answer in its `margins_source` setting. Measuring the book writes the four margins only while it is
 * `measured`, so a margin the reader types must switch it to `manual`, or the next measure would write the typed value
 * over.
 */

/** The name of the setting that holds who sets the margins. */
export const MARGINS_SOURCE = 'margins_source';

/** The values of the setting, as the server writes them. */
export const MarginsSource = {
  Measured: 'measured',
  Manual: 'manual',
} as const;

/** The names of the four margin settings of the normalize step. */
export const MARGIN_SETTINGS: readonly string[] = [
  'margin_top',
  'margin_bottom',
  'margin_inner',
  'margin_outer',
];

/** Tell whether the margins of a step are set by hand. */
export function hasManualMargins(params: Readonly<Record<string, unknown>>): boolean {
  return params[MARGINS_SOURCE] === MarginsSource.Manual;
}

/**
 * Give the settings a form hands back, with the margins marked as set by hand when the reader changed one of them.
 *
 * Only a change of a margin counts, so a change of the page size or of the margin source itself is taken as it is, and a
 * step that has no setting of who sets its margins is never touched.
 *
 * @param before The settings before the change.
 * @param after The settings the form holds now.
 */
export function withMarginsSource(
  before: Readonly<Record<string, unknown>>,
  after: Record<string, unknown>,
): Record<string, unknown> {
  const marginChanged = MARGIN_SETTINGS.some((name) => before[name] !== after[name]);
  const sourceChanged = before[MARGINS_SOURCE] !== after[MARGINS_SOURCE];
  const hasSource = MARGINS_SOURCE in before || MARGINS_SOURCE in after;
  if (!hasSource || !marginChanged || sourceChanged) {
    return after;
  }
  return { ...after, [MARGINS_SOURCE]: MarginsSource.Manual };
}
