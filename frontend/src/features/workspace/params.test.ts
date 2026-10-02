import { describe, expect, it } from 'vitest';
import { CompareMode, PageFilter, parseStageSearch, ViewMode } from '@/features/workspace/params';

describe('parseStageSearch', () => {
  it('reads a complete address', () => {
    expect(
      parseStageSearch({
        page: 'p-1',
        scan: 's-1',
        source: 'f-1',
        view: 'spread',
        compare: 'swipe',
        filter: 'left-out',
      }),
    ).toEqual({
      page: 'p-1',
      scan: 's-1',
      source: 'f-1',
      view: ViewMode.Spread,
      compare: CompareMode.Swipe,
      filter: PageFilter.LeftOut,
    });
  });

  it('leaves out every parameter the address does not have', () => {
    expect(parseStageSearch({})).toEqual({});
  });

  it('accepts every value of the three choices', () => {
    for (const view of ['page', 'spread', 'grid']) {
      expect(parseStageSearch({ view }).view).toBe(view);
    }
    for (const compare of ['off', 'swipe', 'side']) {
      expect(parseStageSearch({ compare }).compare).toBe(compare);
    }
    for (const filter of ['all', 'check', 'left-out']) {
      expect(parseStageSearch({ filter }).filter).toBe(filter);
    }
  });

  it('drops a choice that is none of the values', () => {
    expect(parseStageSearch({ view: 'wall', compare: true, filter: 'Check', page: 'p-1' })).toEqual(
      { page: 'p-1' },
    );
  });

  it('drops a choice that names an inherited property', () => {
    expect(parseStageSearch({ view: 'constructor', filter: '__proto__' })).toEqual({});
  });

  it('writes back an identifier the router decoded as a number', () => {
    expect(parseStageSearch({ page: 12, scan: 7, source: 3 })).toEqual({
      page: '12',
      scan: '7',
      source: '3',
    });
  });

  it('trims an identifier and drops an empty or foreign one', () => {
    expect(parseStageSearch({ page: '  p-1 ', scan: '   ', source: ' f-1' })).toEqual({
      page: 'p-1',
      source: 'f-1',
    });
    expect(parseStageSearch({ page: {}, scan: ['s-1'], source: {} })).toEqual({});
  });
});
