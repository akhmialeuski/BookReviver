import { describe, expect, it } from 'vitest';
import { pageToShow } from './paging';

/**
 * The page the book list shows when the address names a page that does not exist.
 */

describe('pageToShow', () => {
  it.each([
    [1, 5, 1],
    [3, 5, 3],
    [5, 5, 5],
    [6, 5, 5],
    [2, 1, 1],
    [3, 0, 1],
    [1, 0, 1],
  ])('shows page %d of %d pages as page %d', (requested, pages, expected) => {
    expect(pageToShow(requested, pages)).toBe(expected);
  });
});
