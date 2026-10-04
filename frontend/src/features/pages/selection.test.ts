import { describe, expect, it } from 'vitest';
import type { PageSchema } from '@/api';
import { pruneSelection, selectRange } from '@/features/pages/selection';

function page(id: string, position: number): PageSchema {
  return {
    id,
    position,
    label: '',
    label_manual: false,
    section_id: null,
    kind: 'text',
    content_type: 'text',
    content_source: 'kind',
    origin: 'scan',
    scan_id: null,
    source_id: null,
    slot: 0,
    included: true,
    notes: '',
    group_label: '',
    blank_fill: 'scan',
    images: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

const BOOK = ['a', 'b', 'c', 'd', 'e'].map(page);

describe('selectRange', () => {
  it('takes the pages from one to another, ends included', () => {
    expect(selectRange(BOOK, 'b', 'd')).toEqual(['b', 'c', 'd']);
  });

  it('takes the same pages whichever end comes first', () => {
    expect(selectRange(BOOK, 'd', 'b')).toEqual(['b', 'c', 'd']);
  });

  it('takes the target alone when there is no start or the start is gone', () => {
    expect(selectRange(BOOK, null, 'c')).toEqual(['c']);
    expect(selectRange(BOOK, 'gone', 'c')).toEqual(['c']);
  });

  it('takes nothing for a target that is not in the book', () => {
    expect(selectRange(BOOK, 'a', 'gone')).toEqual([]);
  });
});

describe('pruneSelection', () => {
  it('drops the pages that left the book', () => {
    expect([...pruneSelection(new Set(['a', 'gone', 'c']), BOOK)]).toEqual(['a', 'c']);
  });

  it('returns the same set when every page is still there', () => {
    const selected = new Set(['a', 'b']);
    expect(pruneSelection(selected, BOOK)).toBe(selected);
  });
});
