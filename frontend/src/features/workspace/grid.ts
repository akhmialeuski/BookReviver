import { useEffect, useRef, useState } from 'react';
import { isTypingTarget } from '@/features/viewer/keys';
import { browserStorage, type WorkspaceStorage } from '@/features/workspace/storage';

/**
 * The grid over the page of a Geometry step: whether it is shown, the choice that is remembered for the book, and the key
 * that switches it.
 *
 * The grid is a way to see whether the lines of text and the edges of the block lie level, so it stays where the reader
 * left it for the whole book, on every step of Geometry and on the result of the stage, from one step to the next and
 * from one visit to the next. Until the reader chooses, the grid is off everywhere, so nothing appears or goes away by
 * itself when the step changes. The choice lives in the browser, a convenience that is the same as nothing when the
 * browser forbids storage.
 */

/** The key the choice of a book is kept under. */
export const GRID_STORAGE_PREFIX = 'bookreviver.grid';

/** The key that switches the grid. */
export const GRID_KEY = 'g';

/** Give the key the choice of a book is kept under. */
export function gridStorageKey(bookId: string): string {
  return `${GRID_STORAGE_PREFIX}.${bookId}`;
}

/**
 * Read the choice a book kept.
 *
 * @param bookId The book.
 * @param storage Where the choices are kept.
 * @returns Whether the grid is on, or null when the reader has not chosen.
 */
export function readGrid(
  bookId: string,
  storage: WorkspaceStorage = browserStorage,
): boolean | null {
  const stored = storage.getItem(gridStorageKey(bookId));
  return stored === 'on' ? true : stored === 'off' ? false : null;
}

/** Keep the choice of a book. */
export function writeGrid(
  bookId: string,
  on: boolean,
  storage: WorkspaceStorage = browserStorage,
): void {
  storage.setItem(gridStorageKey(bookId), on ? 'on' : 'off');
}

/**
 * Read whether the grid is shown over the page, and switch it.
 *
 * @param bookId The book, whose choice is kept.
 * @returns Whether the grid is on, which it is not until the reader has chosen, and the function that switches it and
 * keeps the choice.
 */
export function useGrid(bookId: string): [boolean, () => void] {
  const [picked, setPicked] = useState<{ bookId: string; on: boolean | null }>(() => ({
    bookId,
    on: readGrid(bookId),
  }));
  // A choice belongs to one book, so the one of another book is read again
  const choice = picked.bookId === bookId ? picked.on : readGrid(bookId);
  const on = choice ?? false;
  const toggle = (): void => {
    writeGrid(bookId, !on);
    setPicked({ bookId, on: !on });
  };
  return [on, toggle];
}

/**
 * Tell whether a key press switches the grid.
 *
 * @param event The key press.
 * @param dialogOpen Whether a dialog is open, whose keys are its own.
 */
export function isGridKey(event: KeyboardEvent, dialogOpen: boolean): boolean {
  return (
    event.key.toLowerCase() === GRID_KEY &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey &&
    !dialogOpen &&
    !isTypingTarget(event.target)
  );
}

/**
 * Switch the grid with the key G while the screen shows it.
 *
 * @param toggle Switch the grid.
 * @param enabled Whether the screen has a grid, which a stage with no steps to level has not.
 */
export function useGridKey(toggle: () => void, enabled: boolean): void {
  const latest = useRef(toggle);
  useEffect(() => {
    latest.current = toggle;
  });
  useEffect(() => {
    if (!enabled) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent): void => {
      if (isGridKey(event, document.querySelector('[role="dialog"]') !== null)) {
        event.preventDefault();
        latest.current();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled]);
}
