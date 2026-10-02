import { describe, expect, it } from 'vitest';
import { carriedBy, dropPlace } from '@/features/order/drag';
import { AnchorSide, movePages } from '@/features/pages/order';
import { page } from '@/features/workspace/fixtures';

const BOOK = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id, index) => page(id, { position: index }));

function reads(carried: string[], activeId: string, overId: string): string {
  const place = dropPlace(BOOK, new Set(carried), activeId, overId);
  const moved = place === null ? null : movePages(BOOK, carried, place);
  return (moved ?? []).map((entry) => entry.id).join('');
}

describe('carriedBy', () => {
  it('carries the whole selection when the held page is selected', () => {
    const selected = new Set(['b', 'c']);
    expect(carriedBy(selected, 'c')).toBe(selected);
  });

  it('carries the held page alone when it is not selected', () => {
    expect([...carriedBy(new Set(['b', 'c']), 'f')]).toEqual(['f']);
  });
});

describe('dropPlace', () => {
  it('puts a page after the page it is dropped on when that page stands later', () => {
    expect(dropPlace(BOOK, new Set(['b']), 'b', 'e')).toEqual({
      pageId: 'e',
      side: AnchorSide.After,
    });
  });

  it('puts a page before the page it is dropped on when that page stands earlier', () => {
    expect(dropPlace(BOOK, new Set(['e']), 'e', 'b')).toEqual({
      pageId: 'b',
      side: AnchorSide.Before,
    });
  });

  it('names no place inside the carried group', () => {
    const carried = new Set(['c', 'd', 'e']);
    for (const over of ['c', 'd', 'e']) {
      expect(dropPlace(BOOK, carried, 'd', over)).toBeNull();
    }
  });

  it('names no place over a page that is not in the book', () => {
    expect(dropPlace(BOOK, new Set(['b']), 'b', 'zz')).toBeNull();
    expect(dropPlace(BOOK, new Set(['zz']), 'zz', 'b')).toBeNull();
  });

  it.each([
    { carried: ['b'], active: 'b', over: 'e', expected: 'acdebfg' },
    { carried: ['e'], active: 'e', over: 'b', expected: 'aebcdfg' },
    { carried: ['c', 'd'], active: 'd', over: 'g', expected: 'abefgcd' },
    { carried: ['c', 'd'], active: 'c', over: 'a', expected: 'cdabefg' },
    { carried: ['b', 'f'], active: 'b', over: 'd', expected: 'acdbfeg' },
    { carried: ['b', 'f'], active: 'f', over: 'd', expected: 'acbfdeg' },
  ])('reads $expected after dragging $carried by $active onto $over', (c) => {
    expect(reads(c.carried, c.active, c.over)).toBe(c.expected);
  });
});
