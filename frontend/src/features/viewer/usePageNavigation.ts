import {
  lastViewStart,
  nextViewStart,
  previousViewStart,
  viewIndexes,
  viewStart,
} from '@/features/viewer/spread';
import { useViewerKeys } from '@/features/viewer/useViewerKeys';

/**
 * Which view of a list of pages is open, which views lie around it, and the calls that turn to another one.
 *
 * The reading mode and the workspace of a stage move through their pages the same way: the open page is named by id,
 * a spread starts on an odd page, the keys turn the view, and the toolbar and the strip open an index. The screens differ
 * in what a page is and in what opening one does, so they pass the list, the way to read an id and the call that opens.
 */

/** The open view of a list of pages and the ways to move it. */
export interface PageNavigation<T> {
  /** Index of the open page, or of the first page while the id names none. */
  currentIndex: number;
  /** Index of the first page of the open view. */
  first: number;
  /** Whether an id is given that no page of the list has. */
  unknownPage: boolean;
  /** The pages of the open view, left to right. */
  shown: T[];
  /** The next and the previous view, each as its pages, for the stage to keep loaded. */
  around: T[][];
  hasPrevious: boolean;
  hasNext: boolean;
  /** Open the view that holds the page at an index, which is clamped into the list. */
  openIndex: (index: number) => void;
  openPrevious: () => void;
  openNext: () => void;
}

/**
 * Work out the open view and listen to the keys that turn it.
 *
 * @param items The pages of the list, in order.
 * @param idOf Gives the id of a page.
 * @param pageId The id of the open page, or undefined for the first one.
 * @param spread Whether two pages are shown at once.
 * @param open Opens the page with an id; it is called with the first page of the view that was asked for.
 */
export function usePageNavigation<T>(
  items: readonly T[],
  idOf: (item: T) => string,
  pageId: string | undefined,
  spread: boolean,
  open: (id: string) => void,
): PageNavigation<T> {
  const count = items.length;
  const foundIndex = pageId === undefined ? -1 : items.findIndex((item) => idOf(item) === pageId);
  const currentIndex = Math.max(foundIndex, 0);
  const indexes = viewIndexes(currentIndex, count, spread);
  const first = indexes[0] ?? 0;
  const previous = previousViewStart(first, count, spread);
  const next = nextViewStart(first, count, spread);

  const openIndex = (index: number): void => {
    const clamped = Math.min(Math.max(index, 0), Math.max(count - 1, 0));
    const target = items[viewStart(clamped, spread)];
    if (target !== undefined) {
      open(idOf(target));
    }
  };
  const openStart = (start: number | null): void => {
    if (start !== null) {
      openIndex(start);
    }
  };

  const openPrevious = (): void => openStart(previous);
  const openNext = (): void => openStart(next);

  useViewerKeys({
    previous: openPrevious,
    next: openNext,
    first: () => openIndex(0),
    last: () => openStart(lastViewStart(count, spread)),
  });

  return {
    currentIndex,
    first,
    unknownPage: pageId !== undefined && foundIndex < 0,
    shown: indexes.flatMap((index) => items[index] ?? []),
    around: [next, previous].flatMap((start) =>
      start === null ? [] : [viewIndexes(start, count, spread).flatMap((i) => items[i] ?? [])],
    ),
    hasPrevious: previous !== null,
    hasNext: next !== null,
    openIndex,
    openPrevious,
    openNext,
  };
}
