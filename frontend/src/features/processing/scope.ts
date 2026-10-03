import type { StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * The pages a run of a stage goes over, and the counts the panel and the menu of the run show.
 *
 * A page that is a placeholder for a scan to come has no image to process, so it is never counted and never sent.
 */

/** Which pages a run goes over. */
export const RunScope = {
  Page: 'page',
  Selected: 'selected',
  Attention: 'attention',
  All: 'all',
} as const;

/** One scope of a run (derived from {@link RunScope}). */
export type RunScope = (typeof RunScope)[keyof typeof RunScope];

/** A scope with the number of pages it covers now. */
export interface ScopeChoice {
  scope: RunScope;
  count: number;
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

/**
 * Give the pages a scope covers.
 *
 * @param scope The scope.
 * @param items Every page of the book with where it stands in the stage.
 * @param currentId The page open on the canvas.
 * @param selectedIds The pages selected in the grid.
 * @returns The identifiers of the pages, or null for every page that has an image, which the server reads as an omitted
 * list and which stays right when pages are added while the run waits.
 */
export function pageIdsFor(
  scope: RunScope,
  items: readonly StripItem[],
  currentId: string | undefined,
  selectedIds: ReadonlySet<string>,
): string[] | null {
  const processable = items.filter(hasImage);
  switch (scope) {
    case RunScope.Page:
      return processable.filter((item) => item.page.id === currentId).map((item) => item.page.id);
    case RunScope.Selected:
      return processable
        .filter((item) => selectedIds.has(item.page.id))
        .map((item) => item.page.id);
    case RunScope.Attention:
      return processable.filter(isTrouble).map((item) => item.page.id);
    case RunScope.All:
      return null;
  }
}

/**
 * Write a scope of the menu of the run, with the number of pages it covers.
 *
 * @param scope The scope.
 * @param count The pages it covers now.
 * @param pageLabel The printed label of the open page, which "This page" names.
 */
export function describeScope(scope: RunScope, count: number, pageLabel: string): string {
  const words = MESSAGES.processing.scope;
  switch (scope) {
    case RunScope.Page:
      return words.page(pageLabel);
    case RunScope.Selected:
      return words.selected(count);
    case RunScope.Attention:
      return words.attention(count);
    case RunScope.All:
      return words.all(count);
  }
}

/** List the scopes of the menu of the run with the number of pages each covers now, in the order the menu has them. */
export function scopeChoices(
  items: readonly StripItem[],
  currentId: string | undefined,
  selectedIds: ReadonlySet<string>,
): ScopeChoice[] {
  const count = (scope: RunScope): number =>
    pageIdsFor(scope, items, currentId, selectedIds)?.length ?? items.filter(hasImage).length;
  return Object.values(RunScope).map((scope) => ({ scope, count: count(scope) }));
}
