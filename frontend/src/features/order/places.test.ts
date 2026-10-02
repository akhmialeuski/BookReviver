import { describe, expect, it } from 'vitest';
import { placesToCheck } from '@/features/order/places';
import { findGaps } from '@/features/pages/gaps';
import { page } from '@/features/workspace/fixtures';

describe('placesToCheck', () => {
  const pages = [
    page('a', { position: 0, label: '1' }),
    page('b', { position: 1, label: '2' }),
    page('c', { position: 2, label: '5' }),
    page('d', { position: 3, label: '6', origin: 'placeholder' }),
    page('e', { position: 4, label: '7', origin: 'placeholder', included: false }),
    page('f', { position: 5, label: '7', origin: 'blank' }),
  ];

  it('lists gaps and missing pages together in book order', () => {
    expect(placesToCheck(pages, findGaps(pages))).toEqual([
      { kind: 'gap', cellId: 'gap-b-c' },
      { kind: 'missing', cellId: 'd' },
    ]);
  });

  it('ignores a missing page that is left out of the book and a blank leaf', () => {
    expect(placesToCheck(pages, []).map((place) => place.cellId)).toEqual(['d']);
  });

  it('lists nothing for a book that has none', () => {
    expect(placesToCheck([], [])).toEqual([]);
  });
});
