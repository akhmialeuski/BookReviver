import { describe, expect, it } from 'vitest';
import type { PageSchema } from '@/api';
import {
  AnchorSide,
  anchorBody,
  anchorOf,
  firstPageOfScan,
  firstPageOfSource,
  movePages,
  pageIdsOfSource,
} from '@/features/pages/order';

function page(id: string, position: number, extra: Partial<PageSchema> = {}): PageSchema {
  return {
    id,
    position,
    label: '',
    kind: 'text',
    origin: 'scan',
    scan_id: null,
    source_id: null,
    slot: 0,
    included: true,
    notes: '',
    images: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...extra,
  };
}

const BOOK = ['a', 'b', 'c', 'd', 'e', 'f'].map((id, index) => page(id, index));

function ids(pages: readonly PageSchema[] | null): string[] {
  return (pages ?? []).map((p) => p.id);
}

describe('movePages', () => {
  it.each([
    { moving: ['a'], anchor: 'c', side: AnchorSide.After, expected: 'bcadef' },
    { moving: ['a'], anchor: 'c', side: AnchorSide.Before, expected: 'bacdef' },
    { moving: ['f'], anchor: 'a', side: AnchorSide.Before, expected: 'fabcde' },
    { moving: ['a'], anchor: 'f', side: AnchorSide.After, expected: 'bcdefa' },
    { moving: ['d'], anchor: 'c', side: AnchorSide.After, expected: 'abcdef' },
    { moving: ['c'], anchor: 'd', side: AnchorSide.Before, expected: 'abcdef' },
  ])('moves $moving $side $anchor', ({ moving, anchor, side, expected }) => {
    expect(ids(movePages(BOOK, moving, { pageId: anchor, side })).join('')).toBe(expected);
  });

  it('keeps a group together in the order it has in the book, whatever order it is named in', () => {
    const moved = movePages(BOOK, ['e', 'b'], { pageId: 'a', side: AnchorSide.Before });
    expect(ids(moved).join('')).toBe('beacdf');
  });

  it('puts a group after an anchor that lies between its pages', () => {
    const moved = movePages(BOOK, ['a', 'f'], { pageId: 'c', side: AnchorSide.After });
    expect(ids(moved).join('')).toBe('bcafde');
  });

  it('renumbers the positions from zero', () => {
    const moved = movePages(BOOK, ['a'], { pageId: 'c', side: AnchorSide.After });
    expect(moved?.map((p) => p.position)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it('leaves the pages it did not touch as they are', () => {
    const moved = movePages(BOOK, ['a'], { pageId: 'c', side: AnchorSide.After });
    expect(moved?.find((p) => p.id === 'e')).toBe(BOOK[4]);
  });

  it('does not change the list it was given', () => {
    movePages(BOOK, ['a'], { pageId: 'c', side: AnchorSide.After });
    expect(ids(BOOK).join('')).toBe('abcdef');
  });

  it('refuses an anchor that is one of the moved pages', () => {
    expect(movePages(BOOK, ['a', 'b'], { pageId: 'b', side: AnchorSide.After })).toBeNull();
  });

  it('refuses an anchor that is not in the book', () => {
    expect(movePages(BOOK, ['a'], { pageId: 'z', side: AnchorSide.After })).toBeNull();
  });

  it('refuses a move of pages that are not in the book', () => {
    expect(movePages(BOOK, ['z'], { pageId: 'a', side: AnchorSide.After })).toBeNull();
    expect(movePages(BOOK, [], { pageId: 'a', side: AnchorSide.After })).toBeNull();
  });

  it('moves the pages of a source after a chosen page', () => {
    const book = [
      page('cover', 0, { source_id: 'cover-file' }),
      page('p1', 1, { source_id: 'body' }),
      page('p2', 2, { source_id: 'body' }),
      page('title', 3, { source_id: 'title-file' }),
      page('p3', 4, { source_id: 'body' }),
    ];
    const moved = movePages(book, pageIdsOfSource(book, 'body'), {
      pageId: 'title',
      side: AnchorSide.After,
    });
    expect(ids(moved)).toEqual(['cover', 'title', 'p1', 'p2', 'p3']);
  });
});

describe('anchorBody and anchorOf', () => {
  it('write a place as exactly one field and read it back', () => {
    expect(anchorBody({ pageId: 'x', side: AnchorSide.Before })).toEqual({ before_page_id: 'x' });
    expect(anchorBody({ pageId: 'x', side: AnchorSide.After })).toEqual({ after_page_id: 'x' });
    expect(anchorOf({ before_page_id: 'x' })).toEqual({ pageId: 'x', side: AnchorSide.Before });
    expect(anchorOf({ after_page_id: 'x', before_page_id: null })).toEqual({
      pageId: 'x',
      side: AnchorSide.After,
    });
  });

  it('read no place from a body that names none or both', () => {
    expect(anchorOf({})).toBeNull();
    expect(anchorOf({ before_page_id: 'x', after_page_id: 'y' })).toBeNull();
  });
});

describe('selecting the pages of a source or a scan', () => {
  const book = [
    page('a', 0, { source_id: 's1', scan_id: 'n1' }),
    page('b', 1, { source_id: 's2', scan_id: 'n2' }),
    page('c', 2, { source_id: 's1', scan_id: 'n3' }),
    page('d', 3),
  ];

  it('lists the pages of a source in book order', () => {
    expect(pageIdsOfSource(book, 's1')).toEqual(['a', 'c']);
    expect(pageIdsOfSource(book, 'nope')).toEqual([]);
  });

  it('finds the first page of a source and of a scan', () => {
    expect(firstPageOfSource(book, 's1')?.id).toBe('a');
    expect(firstPageOfSource(book, 'nope')).toBeUndefined();
    expect(firstPageOfScan(book, 'n3')?.id).toBe('c');
    expect(firstPageOfScan(book, 'nope')).toBeUndefined();
  });
});
