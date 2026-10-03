import { useSyncExternalStore } from 'react';

/**
 * Whether the curves editor shows every node of its grid, which the canvas draws and the button in the panel switches.
 *
 * The canvas and the panel are drawn in two places of the screen and share nothing but the shape, so the choice of the
 * reader to see all the nodes lives here, where both read it. It stays as it was while the reader goes from page to
 * page, since a reader who needed the grid on one page most likely needs it on the next.
 */

let showAll = false;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function read(): boolean {
  return showAll;
}

/** Set whether every node of the grid is shown. */
export function setMoreControl(value: boolean): void {
  showAll = value;
  for (const listener of listeners) {
    listener();
  }
}

/** Follow whether every node of the grid is shown. */
export function useMoreControl(): boolean {
  return useSyncExternalStore(subscribe, read, read);
}
