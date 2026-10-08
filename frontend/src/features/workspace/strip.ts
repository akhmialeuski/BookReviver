import type {
  PageSchema,
  PageVersionSchema,
  RecipeKind,
  StagePageSchema,
  StageSummarySchema,
  StepFlag,
} from '@/api';
import { SourceKind, sourceOfResult } from '@/features/processing/compare';
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

/** A kind of page the strip can be narrowed to, with the pages of the book that are of the kind. */
export interface KindOption {
  kind: RecipeKind;
  pages: number;
}

/**
 * The kinds of page of a stage as the strip and the grid offer them: the choice of one kind to list the pages of. A stage
 * with pages of a single kind has no such view, since it would list every page.
 */
export interface KindView {
  options: readonly KindOption[];
  /** The kind whose pages are listed, or null for every page. */
  selected: RecipeKind | null;
  onSelect: (kind: RecipeKind | null) => void;
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
 * The reasons a page asks for a look at the open step, in the order the filter of the strip lists them. The record makes
 * a flag the server adds a compile error here until it has a place in the list.
 */
const FLAGS_LISTED: Readonly<Record<StepFlag, true>> = {
  unsure: true,
  unusual: true,
  'by-hand': true,
  skipped: true,
};

/** What the strip lists of a flag: the flag and the number of pages that carry it. */
export interface FlagOption {
  flag: StepFlag;
  pages: number;
}

/**
 * The flags of the pages at the open step: the choice of one flag to list the pages of. The server says which flags a
 * page carries, and the strip only filters by them.
 */
export interface FlagView {
  options: readonly FlagOption[];
  /** The flag whose pages are listed, or null for every page. */
  selected: StepFlag | null;
  onSelect: (flag: StepFlag | null) => void;
}

/** What a list of pages needs to be narrowed, which the toolbar, the strip and the grid all take. */
export interface PageListFilters {
  /** The pages of the book, which the filters narrow down. */
  total: number;
  counts: FilterCounts;
  filter: PageFilter;
  /** Whether the filter of the pages cut from wide scans is offered, which the Split stage has. */
  withWide?: boolean;
  /** The kinds of page, which narrow the pages to those of one kind. Absent for none to choose from. */
  kinds?: KindView;
  /** The steps a run stopped at, which narrow the pages to those stopped at one. Absent when no run stopped short. */
  stopped?: StopView;
  /** The reasons a page asks for a look at the open step, which narrow the pages to those with one. Absent for no step. */
  flagged?: FlagView;
  onFilter: (filter: PageFilter) => void;
}

/** What the strip and the grid take to list pages: the pages the filter lists, and the filters that narrowed them. */
export interface PageListProps extends PageListFilters {
  /** The pages the filter lists, in book order. */
  items: readonly StripItem[];
  /** Says why a page asks for a look; the Check filter writes it under the page. Absent for no reasons. */
  reasonOf?: (item: StripItem) => string | null;
}

/** The flags each page carries at the open step, by the identifier of the page. */
export type FlagsByPage = ReadonlyMap<string, readonly StepFlag[]>;

/** Read the flags the server put on the rows of a stage that was asked for a step. */
export function flagsOf(rows: readonly StagePageSchema[]): FlagsByPage {
  return new Map(rows.flatMap((row) => (row.step === null ? [] : [[row.page_id, row.step.flags]])));
}

/** List every flag with the number of pages that carry it, in the order the filter shows them. */
export function flagOptions(flags: FlagsByPage): FlagOption[] {
  const listed = Object.keys(FLAGS_LISTED) as StepFlag[];
  return listed.map((flag) => ({
    flag,
    pages: [...flags.values()].filter((carried) => carried.includes(flag)).length,
  }));
}

/** Keep the pages that carry a flag, or every page for no flag. */
export function applyFlag(
  items: readonly StripItem[],
  flags: FlagsByPage,
  flag: StepFlag | null,
): readonly StripItem[] {
  return flag === null
    ? items
    : items.filter((item) => flags.get(item.page.id)?.includes(flag) === true);
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

/**
 * List the kinds the strip can be narrowed to: the kinds of page the book has, with how many pages each has.
 *
 * @param summary The summary of the stage, or undefined while it is read.
 * @returns The options in the order of the kinds, or none when the book has pages of one kind only.
 */
export function kindOptionsOf(summary: StageSummarySchema | undefined): KindOption[] {
  const present = (summary?.recipes ?? []).filter((entry) => entry.pages > 0);
  return present.length < 2 ? [] : present.map(({ kind, pages }) => ({ kind, pages }));
}

/** Keep the pages of a kind, or every page for no kind. */
export function applyKind(
  items: readonly StripItem[],
  kind: RecipeKind | null,
): readonly StripItem[] {
  return kind === null ? items : items.filter((item) => item.row?.kind === kind);
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
 * Choose the rows the strip, the canvas and the counts are read from.
 *
 * A stage with a step bar always has a step open, and only the rows asked for that step carry the picture of the step, so
 * while they load there are none, and the rows of the stage, whose picture is the result of the stage, never stand in.
 *
 * @param hasBar Whether the stage shows the bar of its steps.
 * @param stepRows The rows asked for the open step, or undefined while they load or when no step is open.
 * @param stageRows The rows of the stage, or undefined while they load.
 * @returns The rows to join with the pages, or undefined while there are none to show.
 */
export function stripRowsOf(
  hasBar: boolean,
  stepRows: readonly StagePageSchema[] | undefined,
  stageRows: readonly StagePageSchema[] | undefined,
): readonly StagePageSchema[] | undefined {
  return hasBar ? stepRows : stageRows;
}

/**
 * Give the version that stands for a page on the strip and on the canvas: the server's `picture` of the row, and nothing
 * else, so the two never disagree. It is what the open step reads, else the last version before it, else what the stage
 * reads; with no step it is the result of the stage, else what the stage reads. Never a later stage, and never the
 * latest result of the book, which `page.images` is.
 *
 * @returns The version, or null while the row loads or when the page has no picture.
 */
export function pictureOf(item: StripItem): PageVersionSchema | null {
  return item.row?.picture ?? null;
}

/**
 * Give the thumbnail of the picture of a page.
 *
 * @returns The path of the thumbnail, or null when the page has no picture yet.
 */
export function thumbnailOf(item: StripItem): string | null {
  return pictureOf(item)?.images?.thumbnail ?? null;
}

/**
 * Give the info document of the pyramid of the picture of a page, which the canvas of the reading layouts draws, or null
 * until the tiles are cut, so the canvas never points at tiles that do not exist yet.
 */
export function canvasSourceOf(item: StripItem): string | null {
  const source = sourceOfResult(pictureOf(item));
  return source?.kind === SourceKind.Iiif ? source.url : null;
}
