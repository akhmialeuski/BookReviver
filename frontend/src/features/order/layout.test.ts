import { describe, expect, it } from 'vitest';
import { gapKey, type LayoutItem, layoutOf } from '@/features/order/layout';
import { findGaps } from '@/features/pages/gaps';
import { page } from '@/features/workspace/fixtures';

function book(count: number) {
  return Array.from({ length: count }, (_, index) => page(`p${index}`, { position: index }));
}

/** The layout as one word per item: the ids of a group joined by `+`, `_` for an empty side, and `GAP` for a card. */
function words(items: readonly LayoutItem[]): string[] {
  return items.map((item) => {
    if (item.kind === 'gap') {
      return 'GAP';
    }
    const ids = item.pages.map((entry) => entry.id).join('+');
    if (item.emptySide === 'left') {
      return `_+${ids}`;
    }
    return item.emptySide === 'right' ? `${ids}+_` : ids;
  });
}

describe('layoutOf pages', () => {
  it('gives each page its own group, in book order', () => {
    expect(words(layoutOf(book(3), [], false))).toEqual(['p0', 'p1', 'p2']);
  });

  it('gives nothing for a book without pages', () => {
    expect(layoutOf([], [], false)).toEqual([]);
    expect(layoutOf([], [], true)).toEqual([]);
  });
});

describe('layoutOf spreads', () => {
  it('keeps the cover alone on the right and pairs an odd page with the even page after it', () => {
    expect(words(layoutOf(book(5), [], true))).toEqual(['_+p0', 'p1+p2', 'p3+p4']);
  });

  it('leaves the last page alone on the left when the pairs run out', () => {
    expect(words(layoutOf(book(4), [], true))).toEqual(['_+p0', 'p1+p2', 'p3+_']);
  });

  it('shows a book of one page as a cover alone', () => {
    expect(words(layoutOf(book(1), [], true))).toEqual(['_+p0']);
  });
});

describe('layoutOf gaps', () => {
  const numbered = book(5).map((entry, index) => ({
    ...entry,
    label: ['1', '2', '5', '6', '7'][index] ?? '',
  }));
  const gaps = findGaps(numbered);

  it('puts the card of a gap before the page the missing numbers lead to', () => {
    expect(words(layoutOf(numbered, gaps, false))).toEqual(['p0', 'p1', 'GAP', 'p2', 'p3', 'p4']);
  });

  it('puts the card before the spread that holds that page', () => {
    // The page the numbers lead to is p2, the second page of the pair p1+p2, so the card stands before that pair
    expect(words(layoutOf(numbered, gaps, true))).toEqual(['_+p0', 'GAP', 'p1+p2', 'p3+p4']);
  });

  it('names the card by the two pages it stands between', () => {
    const [gap] = gaps;
    expect(gap === undefined ? '' : gapKey(gap)).toBe('gap-p1-p2');
  });
});
