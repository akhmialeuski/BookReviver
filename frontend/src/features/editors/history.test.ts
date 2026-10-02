import { describe, expect, it } from 'vitest';
import { popUndo, pushUndo, UNDO_DEPTH, type UndoEntry } from '@/features/editors/history';

/** The stack of changes Ctrl+Z takes back. */

function entry(ownerId: string, previous: UndoEntry['previous'], processorKey = 'geometry.deskew') {
  return { ownerId, processorKey, previous } satisfies UndoEntry;
}

describe('popUndo', () => {
  it('gives the latest change of the edit, with the edit the page had before', () => {
    const stack = pushUndo(pushUndo([], entry('a', null)), entry('a', { degrees: 1 }));

    const taken = popUndo(stack, 'a', 'geometry.deskew');

    expect(taken?.entry.previous).toEqual({ degrees: 1 });
    expect(taken?.rest).toEqual([entry('a', null)]);
  });

  it('gives back the absence of an edit as null', () => {
    const taken = popUndo(pushUndo([], entry('a', null)), 'a', 'geometry.deskew');

    expect(taken?.entry.previous).toBeNull();
  });

  it('leaves the changes of other pages and other processors alone', () => {
    const stack = [entry('b', null), entry('a', { degrees: 2 }, 'other.key')];

    expect(popUndo(stack, 'a', 'geometry.deskew')).toBeNull();
    expect(popUndo(stack, 'b', 'geometry.deskew')?.rest).toEqual([stack[1]]);
  });

  it('has nothing to take back from an empty stack', () => {
    expect(popUndo([], 'a', 'geometry.deskew')).toBeNull();
  });
});

describe('pushUndo', () => {
  it('forgets the oldest change when the stack is full', () => {
    let stack: UndoEntry[] = [];
    for (let count = 0; count < UNDO_DEPTH + 5; count += 1) {
      stack = pushUndo(stack, entry('a', { degrees: count }));
    }

    expect(stack).toHaveLength(UNDO_DEPTH);
    expect(stack[0]?.previous).toEqual({ degrees: 5 });
  });
});
