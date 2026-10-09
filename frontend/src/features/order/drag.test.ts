import type { ClientRect, DroppableContainer } from '@dnd-kit/core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { carriedBy, collideUnderPointer, dropPlace } from '@/features/order/drag';
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

describe('collideUnderPointer', () => {
  type Args = Parameters<typeof collideUnderPointer>[0];

  function rect(left: number, top: number): ClientRect {
    return { left, top, right: left + 100, bottom: top + 100, width: 100, height: 100 };
  }

  /** Two droppables whose measured rectangles are stale: `a` is measured at the top and `b` far below it. */
  function argsWith(pointer: { x: number; y: number } | null): Args {
    return {
      active: { id: 'a' },
      collisionRect: rect(0, 0),
      droppableRects: new Map([
        ['a', rect(0, 0)],
        ['b', rect(0, 1000)],
      ]),
      droppableContainers: [{ id: 'a' }, { id: 'b' }] as unknown as DroppableContainer[],
      pointerCoordinates: pointer,
    } as unknown as Args;
  }

  /** The elements under the pointer, topmost first, as `document.elementsFromPoint` reports them. */
  function pointerOver(...elements: Element[]): void {
    document.elementsFromPoint = vi.fn(() => elements);
  }

  function cell(id: string): HTMLElement {
    const element = document.createElement('div');
    element.dataset.cell = id;
    const inner = document.createElement('span');
    element.append(inner);
    return inner;
  }

  afterEach(() => {
    // jsdom has no `elementsFromPoint`, so the stub of a test is removed rather than restored
    Reflect.deleteProperty(document, 'elementsFromPoint');
  });

  it('names the page whose cell is under the pointer, whatever rectangles dnd-kit measured', () => {
    pointerOver(document.createElement('div'), cell('b'));

    expect(collideUnderPointer(argsWith({ x: 5, y: 5 }))).toEqual([{ id: 'b' }]);
    expect(document.elementsFromPoint).toHaveBeenCalledWith(5, 5);
  });

  it('falls back to the closest center when the pointer is over no registered cell', () => {
    pointerOver(document.createElement('div'), cell('gap-1'));

    expect(collideUnderPointer(argsWith({ x: 5, y: 5 })).map((hit) => hit.id)).toEqual(['a', 'b']);
  });

  it('falls back to the closest center when there is no pointer', () => {
    pointerOver(cell('b'));

    expect(collideUnderPointer(argsWith(null)).map((hit) => hit.id)).toEqual(['a', 'b']);
    expect(document.elementsFromPoint).not.toHaveBeenCalled();
  });
});
