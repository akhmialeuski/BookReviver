import { useState } from 'react';
import { browserStorage, type WorkspaceStorage } from '@/features/workspace/storage';

/**
 * Whether the history of a page is open in the panel of a stage, the choice that is remembered for the viewer.
 *
 * The history is a section of the panel of every stage, so one choice serves every stage, every step and every visit:
 * it stays collapsed until the reader opens it and then stays open until the reader closes it. The choice lives in the
 * browser, a convenience that is the same as nothing when the browser forbids storage, so a storage that throws leaves
 * the section collapsed.
 */

/** The key the choice is kept under. */
export const HISTORY_OPEN_KEY = 'bookreviver.page-history.open';

const OPEN = 'open';
const COLLAPSED = 'collapsed';

/**
 * Read the choice the viewer kept.
 *
 * @param storage Where the choices are kept.
 * @returns Whether the history is open, which is false when nothing was kept or the storage cannot be read.
 */
export function readHistoryOpen(storage: WorkspaceStorage = browserStorage): boolean {
  try {
    return storage.getItem(HISTORY_OPEN_KEY) === OPEN;
  } catch {
    // The storage is blocked, so nothing was ever remembered
    return false;
  }
}

/** Keep the choice of the viewer, or forget it when the storage cannot be written. */
export function writeHistoryOpen(open: boolean, storage: WorkspaceStorage = browserStorage): void {
  try {
    storage.setItem(HISTORY_OPEN_KEY, open ? OPEN : COLLAPSED);
  } catch {
    // The storage is blocked or full, so the next visit starts collapsed
  }
}

/**
 * Read whether the history is open, and change it.
 *
 * @returns Whether the history is open, and the function that sets it and keeps the choice.
 */
export function useHistoryOpen(): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(() => readHistoryOpen());
  const choose = (next: boolean): void => {
    writeHistoryOpen(next);
    setOpen(next);
  };
  return [open, choose];
}
