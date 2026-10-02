import { parseIdentifier } from '@/features/viewer/params';

/**
 * The state of a stage screen that lives in the address: which page or scan is open, how the pages are laid out,
 * whether two results are compared, and which pages the strip lists.
 *
 * The router hands `validateSearch` whatever it parsed from the query string, so every value is checked here and a
 * value of the wrong shape is dropped rather than carried into the screen. A parameter left out means its default,
 * so a link to the plain stage stays short. The page is a `PageId`, never a position, so a link keeps pointing at the
 * same page after pages are moved.
 */

/** How the canvas lays the pages out: one page, a two-page spread, or a grid of many. */
export const ViewMode = {
  Page: 'page',
  Spread: 'spread',
  Grid: 'grid',
} as const;

/** One layout of the canvas (derived from {@link ViewMode}). */
export type ViewMode = (typeof ViewMode)[keyof typeof ViewMode];

/** How the result of the stage is compared with the result before it: not at all, by a swipe, or side by side. */
export const CompareMode = {
  Off: 'off',
  Swipe: 'swipe',
  Side: 'side',
} as const;

/** One way of comparing (derived from {@link CompareMode}). */
export type CompareMode = (typeof CompareMode)[keyof typeof CompareMode];

/** Which pages the strip lists: all of them, the ones to check, or the ones left out of the book. */
export const PageFilter = {
  All: 'all',
  Check: 'check',
  LeftOut: 'left-out',
} as const;

/** One filter of the strip (derived from {@link PageFilter}). */
export type PageFilter = (typeof PageFilter)[keyof typeof PageFilter];

/** What a stage screen keeps in the search params of its route. */
export interface StageSearch {
  /** Identifier of the page that is open, or absent for the first page. */
  page?: string;
  /** Identifier of the scan that is open on the stages that work on scans, or absent for the first scan. */
  scan?: string;
  /** Absent for {@link ViewMode.Page}. */
  view?: ViewMode;
  /** Absent for {@link CompareMode.Off}. */
  compare?: CompareMode;
  /** Absent for {@link PageFilter.All}. */
  filter?: PageFilter;
}

function oneOf<T extends string>(
  values: Readonly<Record<string, T>>,
  value: unknown,
): T | undefined {
  return Object.values(values).find((candidate) => candidate === value);
}

/**
 * Read the search params of a stage route.
 *
 * Identifiers go through {@link parseIdentifier}, and the three choices must be one of their values. Anything else
 * is the same as leaving the parameter out.
 */
export function parseStageSearch(search: Record<string, unknown>): StageSearch {
  const result: StageSearch = {};
  const page = parseIdentifier(search.page);
  const scan = parseIdentifier(search.scan);
  const view = oneOf(ViewMode, search.view);
  const compare = oneOf(CompareMode, search.compare);
  const filter = oneOf(PageFilter, search.filter);
  if (page !== undefined) {
    result.page = page;
  }
  if (scan !== undefined) {
    result.scan = scan;
  }
  if (view !== undefined) {
    result.view = view;
  }
  if (compare !== undefined) {
    result.compare = compare;
  }
  if (filter !== undefined) {
    result.filter = filter;
  }
  return result;
}
