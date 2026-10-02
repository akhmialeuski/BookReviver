import { describe, expect, it } from 'vitest';
import type { PageKind } from '@/api';
import { coverCandidates, coverIdOf } from './cover';
import { pageOf } from './fixtures';

/**
 * The pages the cover picker offers and the page the library shows when no cover is chosen.
 */

function book(...kinds: PageKind[]) {
  return kinds.map((kind, index) => pageOf(`p${index + 1}`, kind));
}

describe('coverIdOf', () => {
  it('gives the chosen cover', () => {
    expect(coverIdOf(book('cover', 'text'), 'p2')).toBe('p2');
  });

  it('falls back to the first page, as the library does', () => {
    expect(coverIdOf(book('cover', 'text'), null)).toBe('p1');
  });

  it('gives nothing for a book without pages', () => {
    expect(coverIdOf([], null)).toBeNull();
  });
});

describe('coverCandidates', () => {
  const pages = book('text', 'cover', 'text', 'title', 'text', 'plate', 'text');

  it('offers the pages that look like a cover and the first page', () => {
    const numbers = coverCandidates(pages, null, false).map((entry) => entry.number);

    expect(numbers).toEqual([1, 2, 4, 6]);
  });

  it('offers the chosen page even when it looks like a plain page', () => {
    const numbers = coverCandidates(pages, 'p5', false).map((entry) => entry.number);

    expect(numbers).toEqual([1, 2, 4, 5, 6]);
  });

  it('offers every page in book order on request', () => {
    const numbers = coverCandidates(pages, null, true).map((entry) => entry.number);

    expect(numbers).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it('offers nothing for a book without pages', () => {
    expect(coverCandidates([], null, false)).toEqual([]);
  });
});
