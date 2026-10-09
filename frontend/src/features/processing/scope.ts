import type { ContentType } from '@/api';
import type { StripItem } from '@/features/workspace/strip';

/**
 * The pages a run of a stage goes over, and the counts the menu of the run shows.
 *
 * A page that is a placeholder for a scan to come has no image to process, so it is never counted and never sent.
 */

/** Which pages a run goes over. */
export const RunScope = {
  Page: 'page',
  FromPage: 'from-page',
  Selected: 'selected',
  Group: 'group',
  Attention: 'attention',
  All: 'all',
} as const;

/** One scope of a run (derived from {@link RunScope}). */
export type RunScope = (typeof RunScope)[keyof typeof RunScope];

/** The group of pages that shows blank paper, which the kind of the page tells and the content type does not. */
export const BLANK_GROUP = 'blank';

/** A kind of pages the menu of the run names: what the pages show, or the blank pages. */
export type PageGroup = ContentType | typeof BLANK_GROUP;

/** The groups in the order the menu lists them. */
const GROUPS: readonly PageGroup[] = ['text', 'color-picture', 'bw-picture', BLANK_GROUP];

/** A scope with the pages it covers now. */
export interface ScopeChoice {
  scope: RunScope;
  /** The group of pages, for the scope of a group. */
  group?: PageGroup;
  /** The pages it covers, in book order, which have an image. */
  items: readonly StripItem[];
}

/** What stands in the way of a stage on the pages: results out of date and runs that failed. */
export interface Trouble {
  stale: number;
  failed: number;
}

function hasImage(item: StripItem): boolean {
  return item.page.origin !== 'placeholder';
}

function isTrouble(item: StripItem): boolean {
  return item.row?.status === 'stale' || item.row?.status === 'failed';
}

/** Count the pages of the stage whose result is out of date and those the stage failed on. */
export function troubleOf(items: readonly StripItem[]): Trouble {
  const processable = items.filter(hasImage);
  return {
    stale: processable.filter((item) => item.row?.status === 'stale').length,
    failed: processable.filter((item) => item.row?.status === 'failed').length,
  };
}

function inGroup(item: StripItem, group: PageGroup): boolean {
  return group === BLANK_GROUP ? item.page.kind === 'blank' : item.page.content_type === group;
}

/**
 * Give the pages a scope covers, in book order.
 *
 * @param scope The scope.
 * @param items Every page of the book with where it stands in the stage, in book order.
 * @param currentId The page open on the canvas.
 * @param selectedIds The pages selected in the grid.
 * @param group The group of pages the scope of a group goes over, which the other scopes ignore.
 * @returns The pages that have an image.
 */
function itemsOf(
  scope: RunScope,
  items: readonly StripItem[],
  currentId: string | undefined,
  selectedIds: ReadonlySet<string>,
  group?: PageGroup,
): StripItem[] {
  const processable = items.filter(hasImage);
  switch (scope) {
    case RunScope.Page:
      return processable.filter((item) => item.page.id === currentId);
    case RunScope.FromPage: {
      const at = processable.findIndex((item) => item.page.id === currentId);
      return at < 0 ? [] : processable.slice(at);
    }
    case RunScope.Selected:
      return processable.filter((item) => selectedIds.has(item.page.id));
    case RunScope.Group:
      return group === undefined ? [] : processable.filter((item) => inGroup(item, group));
    case RunScope.Attention:
      return processable.filter(isTrouble);
    case RunScope.All:
      return processable;
  }
}

/**
 * Give the identifiers of the pages a scope covers.
 *
 * @param scope The scope.
 * @param items Every page of the book with where it stands in the stage.
 * @param currentId The page open on the canvas.
 * @param selectedIds The pages selected in the grid.
 * @param group The group of pages the scope of a group goes over.
 * @returns The identifiers of the pages, or null for every page that has an image, which the server reads as an omitted
 * list and which stays right when pages are added while the run waits.
 */
export function pageIdsFor(
  scope: RunScope,
  items: readonly StripItem[],
  currentId: string | undefined,
  selectedIds: ReadonlySet<string>,
  group?: PageGroup,
): string[] | null {
  return scope === RunScope.All
    ? null
    : itemsOf(scope, items, currentId, selectedIds, group).map((item) => item.page.id);
}

/** Tell whether a result of the stage was made on a page, whether or not it is out of date now. */
export function hasResult(item: StripItem): boolean {
  return item.row?.status === 'fresh' || item.row?.status === 'stale';
}

/**
 * List the choices of the menu of the run with the pages each covers now, in the order the menu has them: the
 * open page, the pages from it on, the selected pages, one choice for each group of pages the book has, the pages that
 * need a look and every page.
 *
 * @param items Every page of the book with where it stands in the stage, in book order.
 * @param currentId The page open on the canvas.
 * @param selectedIds The pages selected in the grid.
 */
export function scopeChoices(
  items: readonly StripItem[],
  currentId: string | undefined,
  selectedIds: ReadonlySet<string>,
): ScopeChoice[] {
  const choiceOf = (scope: RunScope, group?: PageGroup): ScopeChoice => ({
    scope,
    group,
    items: itemsOf(scope, items, currentId, selectedIds, group),
  });
  return [
    choiceOf(RunScope.Page),
    choiceOf(RunScope.FromPage),
    choiceOf(RunScope.Selected),
    ...GROUPS.map((group) => choiceOf(RunScope.Group, group)).filter(
      ({ items }) => items.length > 0,
    ),
    choiceOf(RunScope.Attention),
    choiceOf(RunScope.All),
  ];
}
