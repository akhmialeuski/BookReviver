import {
  addFiles,
  type Direction,
  moveFile,
  moveFolder,
  type PickedFile,
  removeFile,
  type SkippedFile,
  sortNaturally,
  type UploadFile,
} from '@/features/projects/upload/files';

/**
 * The state of the upload dialog's file list as a reducer, so every change of it is one tested function and the
 * component only dispatches what the person did.
 */

export interface Selection {
  files: UploadFile[];
  /** Files left out, kept so the dialog can name them, without repeats. */
  skipped: SkippedFile[];
}

export const EMPTY_SELECTION: Selection = { files: [], skipped: [] };

export type SelectionAction =
  | { type: 'add'; picked: readonly PickedFile[] }
  | { type: 'remove'; path: string }
  | { type: 'move-file'; path: string; direction: Direction }
  | { type: 'move-folder'; groupIndex: number; direction: Direction }
  | { type: 'sort' }
  | { type: 'clear' };

function mergeSkipped(known: readonly SkippedFile[], added: readonly SkippedFile[]): SkippedFile[] {
  const seen = new Set(known.map((entry) => `${entry.reason}:${entry.path}`));
  const fresh = added.filter((entry) => !seen.has(`${entry.reason}:${entry.path}`));
  return [...known, ...fresh];
}

export function selectionReducer(state: Selection, action: SelectionAction): Selection {
  switch (action.type) {
    case 'add': {
      const { files, skipped } = addFiles(state.files, action.picked);
      return { files, skipped: mergeSkipped(state.skipped, skipped) };
    }
    case 'remove':
      return { ...state, files: removeFile(state.files, action.path) };
    case 'move-file':
      return { ...state, files: moveFile(state.files, action.path, action.direction) };
    case 'move-folder':
      return { ...state, files: moveFolder(state.files, action.groupIndex, action.direction) };
    case 'sort':
      return { ...state, files: sortNaturally(state.files) };
    case 'clear':
      return EMPTY_SELECTION;
  }
}
