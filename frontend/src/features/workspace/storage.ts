import type { Stage } from '@/api';
import { parseStage } from '@/features/stages/parse';

/**
 * What the workspace keeps in the browser: the stage a book was left on and the widths of its panels.
 *
 * `localStorage` throws when the browser forbids it, such as in some private windows, and when its quota is full.
 * A remembered width is a convenience, so a failure is the same as having nothing remembered.
 */

/** The part of `Storage` the workspace uses, which `react-resizable-panels` accepts as its layout storage. */
export type WorkspaceStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** The browser's local storage, with every failure turned into "nothing there". */
export const browserStorage: WorkspaceStorage = {
  getItem(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      // Storage is blocked, so nothing was ever remembered
      return null;
    }
  },
  setItem(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      // Storage is blocked or full, so the next visit starts from the defaults
    }
  },
};

function lastStageKey(projectId: string): string {
  return `bookreviver.lastStage.${projectId}`;
}

/** Remember the stage a book was last opened on. */
export function rememberStage(
  projectId: string,
  stage: Stage,
  storage: WorkspaceStorage = browserStorage,
): void {
  storage.setItem(lastStageKey(projectId), stage);
}

/** Give the stage a book was last opened on, or null when none is remembered or the record is not a stage. */
export function recallStage(
  projectId: string,
  storage: WorkspaceStorage = browserStorage,
): Stage | null {
  return parseStage(storage.getItem(lastStageKey(projectId)));
}
