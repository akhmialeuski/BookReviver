import type { PageSchema, StagePageSchema } from '@/api';
import { PageFilter } from '@/features/workspace/params';

/**
 * The rows of the page strip: the pages of the book joined with where each stands in the open stage, and the filters
 * that narrow them.
 *
 * The rows of a stage hold no label and no flag of inclusion, so the manifest, which the viewer and the stage share,
 * supplies them, and the join is by page identifier.
 */

/** One page of the strip. */
export interface StripItem {
  page: PageSchema;
  /** The state of the page in the open stage, or undefined while the rows of the stage are still loading. */
  row: StagePageSchema | undefined;
  /** Whether the page is cut from a scan wider than tall, which only the Split stage looks for. */
  wide?: boolean;
}

/** How many pages each filter of the strip would list. */
export type FilterCounts = Record<PageFilter, number>;

/** What a page shows of the variant of the recipe it was processed by. */
export interface VariantMark {
  name: string;
  /** The class that paints the dot of the variant. */
  tone: string;
  /** Whether the variant is pinned to the page. */
  pinned: boolean;
}

/** A variant the strip can be narrowed to, with the pages it processed. */
export interface VariantOption {
  id: string;
  name: string;
  pages: number;
}

/**
 * The variants of a stage as the strip and the grid draw them: a mark on each page, and the choice of one variant to
 * list the pages of. A stage with a single recipe has no such view, since every mark would be the same.
 */
export interface VariantView {
  /** The mark of a page, or null for a page no recipe processed. */
  markOf: (item: StripItem) => VariantMark | null;
  options: readonly VariantOption[];
  /** The variant whose pages are listed, or null for every page. */
  selected: string | null;
  onSelect: (id: string | null) => void;
}

/** A step the strip can be narrowed to, with the pages a run of the stage stopped at it. */
export interface StopOption {
  /** Index of the step in the recipe, from zero. */
  step: number;
  pages: number;
}

/**
 * The steps a run of the stage stopped at: the choice of one step to list the pages of. A stage no run stopped short of
 * the last step on has no such view.
 */
export interface StopView {
  options: readonly StopOption[];
  /** The step whose pages are listed, or null for every page. */
  selected: number | null;
  onSelect: (step: number | null) => void;
}

/**
 * Join the pages of the book with the rows of a stage.
 *
 * @param pages The pages of the book in book order.
 * @param rows The rows of the stage, in any order.
 * @param wideScans The identifiers of the scans wider than tall, for the stage that looks for them.
 * @returns One item per page in book order; a page without a row has an undefined row.
 */
export function joinRows(
  pages: readonly PageSchema[],
  rows: readonly StagePageSchema[],
  wideScans: ReadonlySet<string> = new Set(),
): StripItem[] {
  const rowOf = new Map(rows.map((row) => [row.page_id, row]));
  return pages.map((page) => ({
    page,
    row: rowOf.get(page.id),
    wide: page.scan_id !== null && wideScans.has(page.scan_id),
  }));
}

/** Tell whether a page asks for a look in the stage: its result is out of date, failed or marked as unsure. */
export function needsCheck(item: StripItem): boolean {
  const { row } = item;
  return (
    row !== undefined && (row.status === 'stale' || row.status === 'failed' || row.review !== null)
  );
}

/** Tell whether the user marked bad the result the page stands on: of the open step, or of the stage when none is open. */
export function isMarkedBad(item: StripItem): boolean {
  return item.row?.marked_bad === true;
}

/** Tell whether the result of a page was made through some of the steps of its recipe only. */
function stoppedAt(item: StripItem): number | null {
  const { row } = item;
  return row === undefined || row.status === 'failed' ? null : row.through_step;
}

/** List the steps a run stopped at with the pages of each, the first step first. */
export function stopOptions(items: readonly StripItem[]): StopOption[] {
  const counts = new Map<number, number>();
  for (const item of items) {
    const step = stoppedAt(item);
    if (step !== null) {
      counts.set(step, (counts.get(step) ?? 0) + 1);
    }
  }
  return [...counts].map(([step, pages]) => ({ step, pages })).sort((a, b) => a.step - b.step);
}

/** Keep the pages a run stopped at a step, or every page for no step. */
export function applyStopped(
  items: readonly StripItem[],
  step: number | null,
): readonly StripItem[] {
  return step === null ? items : items.filter((item) => stoppedAt(item) === step);
}

/** Tell whether a page is kept in the book but not part of it. */
export function isLeftOut(item: StripItem): boolean {
  return !item.page.included;
}

/** Tell whether a page is cut from a scan wider than tall. */
export function isWide(item: StripItem): boolean {
  return item.wide === true;
}

const FILTERS: Readonly<Record<PageFilter, (item: StripItem) => boolean>> = {
  [PageFilter.All]: () => true,
  [PageFilter.Check]: needsCheck,
  [PageFilter.Bad]: isMarkedBad,
  [PageFilter.LeftOut]: isLeftOut,
  [PageFilter.Wide]: isWide,
};

/** Keep the items a filter lists, in their order. */
export function applyFilter(items: readonly StripItem[], filter: PageFilter): StripItem[] {
  return items.filter(FILTERS[filter]);
}

/** Count what each filter lists, for the numbers on the filter buttons. */
export function countFilters(items: readonly StripItem[]): FilterCounts {
  return {
    [PageFilter.All]: items.length,
    [PageFilter.Check]: items.filter(needsCheck).length,
    [PageFilter.Bad]: items.filter(isMarkedBad).length,
    [PageFilter.LeftOut]: items.filter(isLeftOut).length,
    [PageFilter.Wide]: items.filter(isWide).length,
  };
}

/**
 * Give the picture that stands for a page in a stage: the thumbnail of the result of the stage, else the thumbnail of
 * the page itself.
 *
 * @returns The path of the thumbnail, or null when neither exists yet.
 */
export function thumbnailOf(item: StripItem): string | null {
  return item.row?.version?.images?.thumbnail ?? item.page.images?.thumbnail ?? null;
}

/**
 * Give the info document of the pyramid the canvas draws for a page: the result of the stage once its tiles are cut,
 * else the image of the page itself, so the canvas never points at tiles that do not exist yet.
 */
export function canvasSourceOf(item: StripItem): string | null {
  const version = item.row?.version;
  if (version?.tiles_ready && version.images !== null) {
    return version.images.iiif_info;
  }
  return item.page.images?.iiif_info ?? null;
}
