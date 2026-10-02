import type { PageVersionSchema } from '@/api';

/**
 * What a step did to a page, read from the data of its version, and the history of the results a stage has made on a
 * page.
 *
 * The data of a version is a map the processor fills, so each value is read as the number or the flag it should be and
 * is left out when it is not, rather than shown as it came.
 */

/** What the step of a version found on its page; a fact the step did not report is null. */
export interface PageResult {
  /** The angle in degrees the page was turned by. */
  angle: number | null;
  /** How sure the step is of what it found, from 0 to 1. */
  confidence: number | null;
  /** Whether the step left the image as it was. */
  skipped: boolean;
  /** The distance in pixels of the cut from the left edge of the scan. */
  cutX: number | null;
  /** The distance in pixels of the cut from the left edge at the top row, which is not the bottom one for a slanted cut. */
  cutTopX: number | null;
  /** The distance in pixels of the cut from the left edge at the bottom row of the scan. */
  cutBottomX: number | null;
  /** The pixels each half reaches over the cut. */
  overlapPx: number | null;
  /** How many pages the scan was split into, one or two. */
  pages: number | null;
  /** The angle of the cut from the vertical in degrees, positive when it leans right going down. */
  slantDeg: number | null;
}

function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** The slant of a cut that runs from the top to the bottom row of an image, or null when a number is missing. */
function slantOf(top: number | null, bottom: number | null, height: number | null): number | null {
  if (top === null || bottom === null || height === null) {
    return null;
  }
  return (Math.atan2(bottom - top, Math.max(height - 1, 1)) * 180) / Math.PI;
}

/** Read what the step of a version found. */
export function readResult(version: Pick<PageVersionSchema, 'data'>): PageResult {
  const { data } = version;
  return {
    angle: numberOf(data.angle),
    confidence: numberOf(data.confidence),
    skipped: data.skipped === true,
    cutX: numberOf(data.cut_x),
    cutTopX: numberOf(data.cut_top_x),
    cutBottomX: numberOf(data.cut_bottom_x),
    overlapPx: numberOf(data.overlap_px),
    pages: numberOf(data.pages),
    slantDeg: slantOf(
      numberOf(data.cut_top_x),
      numberOf(data.cut_bottom_x),
      numberOf(data.height_px),
    ),
  };
}

/** One result of a stage on a page, and whether it is the one the stage stands on now. */
export interface HistoryEntry {
  version: PageVersionSchema;
  current: boolean;
}

/**
 * List the results of a page that a reader may go back to, the newest first.
 *
 * Only a ready result of a full run can be made the current one, which is what the server accepts, so a preview and a
 * failed run are left out.
 *
 * @param versions The versions of the page in the stage, in any order.
 * @param currentId The current version of the stage on the page, if there is one.
 */
export function historyOf(
  versions: readonly PageVersionSchema[],
  currentId: string | undefined,
): HistoryEntry[] {
  return versions
    .filter((version) => version.state === 'ready' && version.scale === 'full')
    .toSorted((a, b) => b.created_at.localeCompare(a.created_at))
    .map((version) => ({ version, current: version.id === currentId }));
}

/** Write a field name the way a reader would, when the schema gives it no title: `max_angle` as `Max angle`. */
function humanised(name: string): string {
  const words = name.replaceAll('_', ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Give the label of one parameter: the title of its field in the schema.
 *
 * @param name The name of the parameter.
 * @param parameters The JSON Schema of the processor.
 */
export function parameterLabel(
  name: string,
  parameters: Readonly<Record<string, unknown>>,
): string {
  const properties = parameters.properties;
  const property =
    typeof properties === 'object' && properties !== null
      ? (properties as Record<string, unknown>)[name]
      : undefined;
  const title =
    typeof property === 'object' && property !== null
      ? (property as Record<string, unknown>).title
      : undefined;
  return typeof title === 'string' && title !== '' ? title : humanised(name);
}

/**
 * Write the parameters of a result as a line a reader can compare with another, with the titles of the schema.
 *
 * @param params The parameters the version recorded.
 * @param parameters The JSON Schema of the processor that made it.
 * @returns The parts of the line, one for each parameter, in the order the schema lists the fields.
 */
export function describeParams(
  params: Readonly<Record<string, unknown>>,
  parameters: Readonly<Record<string, unknown>>,
): { label: string; value: string }[] {
  const properties = parameters.properties;
  const order =
    typeof properties === 'object' && properties !== null ? Object.keys(properties) : [];
  const names = [...Object.keys(params)].sort((a, b) => {
    const rank = (name: string): number => {
      const index = order.indexOf(name);
      return index < 0 ? order.length : index;
    };
    return rank(a) - rank(b);
  });
  return names.map((name) => ({
    label: parameterLabel(name, parameters),
    value: String(params[name]),
  }));
}
