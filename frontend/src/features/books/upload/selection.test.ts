import { describe, expect, it } from 'vitest';
import { Direction, SkipReason } from './files';
import { EMPTY_SELECTION, selectionReducer } from './selection';

/**
 * The reducer of the file list: picks accumulate, skipped files are named once, and clearing starts over.
 */

function pick(...paths: string[]): { type: 'add'; picked: { path: string; file: File }[] } {
  return { type: 'add', picked: paths.map((path) => ({ path, file: new File(['x'], path) })) };
}

describe('selectionReducer', () => {
  it('accumulates picks and names a skipped file once however often it is picked', () => {
    let state = selectionReducer(EMPTY_SELECTION, pick('b/Thumbs.db', 'b/1.tif'));
    state = selectionReducer(state, pick('b/Thumbs.db', 'b/2.tif'));

    expect(state.files.map((file) => file.path)).toEqual(['b/1.tif', 'b/2.tif']);
    expect(state.skipped).toEqual([{ path: 'b/Thumbs.db', reason: SkipReason.SystemFile }]);
  });

  it('removes a file and moves files and folders', () => {
    let state = selectionReducer(EMPTY_SELECTION, pick('v1/1.tif', 'v1/2.tif', 'v2/1.tif'));

    state = selectionReducer(state, { type: 'remove', path: 'v1/2.tif' });
    expect(state.files.map((file) => file.path)).toEqual(['v1/1.tif', 'v2/1.tif']);

    state = selectionReducer(state, {
      type: 'move-folder',
      groupIndex: 1,
      direction: Direction.Up,
    });
    expect(state.files.map((file) => file.path)).toEqual(['v2/1.tif', 'v1/1.tif']);

    state = selectionReducer(state, {
      type: 'move-file',
      path: 'v2/1.tif',
      direction: Direction.Down,
    });
    expect(state.files.map((file) => file.path)).toEqual(['v1/1.tif', 'v2/1.tif']);
  });

  it('sorts a moved list back into natural order', () => {
    let state = selectionReducer(EMPTY_SELECTION, pick('1.tif', '2.tif'));
    state = selectionReducer(state, {
      type: 'move-file',
      path: '1.tif',
      direction: Direction.Down,
    });

    state = selectionReducer(state, { type: 'sort' });

    expect(state.files.map((file) => file.path)).toEqual(['1.tif', '2.tif']);
  });

  it('clears the files and the skipped list together', () => {
    const state = selectionReducer(selectionReducer(EMPTY_SELECTION, pick('a.tif', 'a.txt')), {
      type: 'clear',
    });

    expect(state).toEqual(EMPTY_SELECTION);
  });
});
