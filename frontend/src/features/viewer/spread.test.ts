import { describe, expect, it } from 'vitest';
import {
  lastViewStart,
  nextViewStart,
  previousViewStart,
  viewIndexes,
  viewStart,
} from '@/features/viewer/spread';

describe('viewIndexes', () => {
  it.each([
    { index: 0, count: 6, expected: [0] },
    { index: 3, count: 6, expected: [3] },
    { index: 99, count: 6, expected: [5] },
  ])('shows one page of $count at $index', ({ index, count, expected }) => {
    expect(viewIndexes(index, count, false)).toEqual(expected);
  });

  it.each([
    { index: 0, count: 6, expected: [0] },
    { index: 1, count: 6, expected: [1, 2] },
    { index: 2, count: 6, expected: [1, 2] },
    { index: 3, count: 6, expected: [3, 4] },
    { index: 4, count: 6, expected: [3, 4] },
    { index: 5, count: 6, expected: [5] },
  ])('pairs an odd page with the even one after it, for $index of $count', (row) => {
    expect(viewIndexes(row.index, row.count, true)).toEqual(row.expected);
  });

  it('shows the last page of an even count alone', () => {
    expect(viewIndexes(4, 5, true)).toEqual([3, 4]);
    expect(viewIndexes(6, 7, true)).toEqual([5, 6]);
  });

  it('gives nothing for a book without pages', () => {
    expect(viewIndexes(0, 0, true)).toEqual([]);
  });
});

describe('viewStart', () => {
  it('never goes below the first page', () => {
    expect(viewStart(-3, true)).toBe(0);
    expect(viewStart(-3, false)).toBe(0);
  });
});

describe('previousViewStart and nextViewStart', () => {
  it('step one page at a time for a single view', () => {
    expect(previousViewStart(3, 6, false)).toBe(2);
    expect(nextViewStart(3, 6, false)).toBe(4);
  });

  it('step a pair at a time for a spread', () => {
    expect(nextViewStart(1, 8, true)).toBe(3);
    expect(nextViewStart(3, 8, true)).toBe(5);
    expect(previousViewStart(5, 8, true)).toBe(3);
    expect(previousViewStart(3, 8, true)).toBe(1);
  });

  it('go from the cover to the first pair and back', () => {
    expect(nextViewStart(0, 8, true)).toBe(1);
    expect(previousViewStart(1, 8, true)).toBe(0);
    expect(previousViewStart(2, 8, true)).toBe(0);
  });

  it('end at the first and the last view', () => {
    expect(previousViewStart(0, 8, true)).toBeNull();
    expect(previousViewStart(0, 8, false)).toBeNull();
    expect(nextViewStart(7, 8, false)).toBeNull();
    expect(nextViewStart(7, 8, true)).toBeNull();
    // Pages 5 and 6 form a pair, so the last page of eight stands alone after it
    expect(nextViewStart(6, 8, true)).toBe(7);
    expect(nextViewStart(6, 7, true)).toBeNull();
  });

  it('have no neighbours in a book without pages', () => {
    expect(previousViewStart(0, 0, true)).toBeNull();
    expect(nextViewStart(0, 0, true)).toBeNull();
  });
});

describe('lastViewStart', () => {
  it.each([
    { count: 0, spread: false, expected: null },
    { count: 5, spread: false, expected: 4 },
    { count: 5, spread: true, expected: 3 },
    { count: 6, spread: true, expected: 5 },
    { count: 1, spread: true, expected: 0 },
  ])('is $expected for $count pages, spread $spread', ({ count, spread, expected }) => {
    expect(lastViewStart(count, spread)).toBe(expected);
  });
});
