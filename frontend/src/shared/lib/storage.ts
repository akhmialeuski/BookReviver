/**
 * What the workspace keeps in the browser: the widths of its panels.
 *
 * The widths are a property of the screen and not of the book, so they stay in the browser, while the place of a book
 * lives on the server (`features/place`). `localStorage` throws when the browser forbids it, such as in some private
 * windows, and when its quota is full. A remembered width is a convenience, so a failure is the same as having nothing
 * remembered.
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
