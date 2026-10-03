import type { Geometry } from '@/features/editors/shapes';

/**
 * The edits a reader can take back with Ctrl+Z: for each change, the edit the page had before it, or none.
 *
 * Taking back restores that edit, or deletes the edit when the page had none, so what the reader gets back is what the
 * page was. The stack belongs to the open screen and is lost with it, since a restored edit is a change like any other.
 */

/** The most changes remembered. */
export const UNDO_DEPTH = 50;

/** One change that can be taken back. */
export interface UndoEntry {
  /** The page the edit belongs to. */
  ownerId: string;
  /** The step of the recipe that reads the edit. */
  stepId: string;
  /** What the page had before the change, or null for no edit. */
  previous: Geometry | null;
}

/** Put a change on the stack, dropping the oldest when the stack is full. */
export function pushUndo(stack: readonly UndoEntry[], entry: UndoEntry): UndoEntry[] {
  return [...stack, entry].slice(-UNDO_DEPTH);
}

/**
 * Take the latest change of an edit off the stack.
 *
 * @param stack The stack.
 * @param ownerId The page.
 * @param stepId The step.
 * @returns The change and the stack without it, or null when the edit has nothing to take back.
 */
export function popUndo(
  stack: readonly UndoEntry[],
  ownerId: string,
  stepId: string,
): { entry: UndoEntry; rest: UndoEntry[] } | null {
  const index = stack.findLastIndex(
    (entry) => entry.ownerId === ownerId && entry.stepId === stepId,
  );
  const entry = stack[index];
  if (entry === undefined) {
    return null;
  }
  return { entry, rest: stack.filter((_, at) => at !== index) };
}
