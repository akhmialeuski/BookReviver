import { describe, expect, it } from 'vitest';
import type { PageSchema } from '@/api';
import { type SelectionState, selectionAfterClick } from '@/features/workspace/selection';

function pages(...ids: string[]): PageSchema[] {
  return ids.map(
    (id, position) =>
      ({
        id,
        position,
        label: '',
        label_manual: false,
        kind: 'text',
        origin: 'scan',
        scan_id: null,
        source_id: null,
        slot: 0,
        included: true,
        notes: '',
        group_label: '',
        images: null,
        created_at: '',
        updated_at: '',
      }) satisfies PageSchema,
  );
}

const BOOK = pages('a', 'b', 'c', 'd', 'e');
const NONE: SelectionState = { selected: new Set(), anchorId: null };
const PLAIN = { range: false, toggle: false };

describe('selectionAfterClick', () => {
  it('selects the page of a plain click alone and makes it the anchor', () => {
    const next = selectionAfterClick(
      BOOK,
      { selected: new Set(['a', 'b']), anchorId: 'a' },
      'd',
      PLAIN,
    );
    expect([...next.selected]).toEqual(['d']);
    expect(next.anchorId).toBe('d');
  });

  it('adds a page on a click with Ctrl and takes it out on the next', () => {
    const added = selectionAfterClick(BOOK, { selected: new Set(['a']), anchorId: 'a' }, 'c', {
      range: false,
      toggle: true,
    });
    expect([...added.selected]).toEqual(['a', 'c']);

    const removed = selectionAfterClick(BOOK, added, 'c', { range: false, toggle: true });
    expect([...removed.selected]).toEqual(['a']);
  });

  it('selects the range from the anchor to the page clicked, in either direction', () => {
    const forward = selectionAfterClick(BOOK, { selected: new Set(['b']), anchorId: 'b' }, 'd', {
      range: true,
      toggle: false,
    });
    expect([...forward.selected]).toEqual(['b', 'c', 'd']);

    const backward = selectionAfterClick(BOOK, { selected: new Set(['d']), anchorId: 'd' }, 'b', {
      range: true,
      toggle: false,
    });
    expect([...backward.selected]).toEqual(['b', 'c', 'd']);
  });

  it('keeps the anchor on a range so it can be stretched again', () => {
    const first = selectionAfterClick(BOOK, { selected: new Set(['b']), anchorId: 'b' }, 'c', {
      range: true,
      toggle: false,
    });
    const second = selectionAfterClick(BOOK, first, 'e', { range: true, toggle: false });
    expect(first.anchorId).toBe('b');
    expect([...second.selected]).toEqual(['b', 'c', 'd', 'e']);
  });

  it('adds a range to what was selected on Shift with Ctrl', () => {
    const next = selectionAfterClick(BOOK, { selected: new Set(['a']), anchorId: 'c' }, 'e', {
      range: true,
      toggle: true,
    });
    expect([...next.selected].sort()).toEqual(['a', 'c', 'd', 'e']);
  });

  it('selects only the page clicked on a range without an anchor', () => {
    const next = selectionAfterClick(BOOK, NONE, 'c', { range: true, toggle: false });
    expect([...next.selected]).toEqual(['c']);
    expect(next.anchorId).toBe('c');
  });
});
