import { describe, expect, it } from 'vitest';
import { insertBody, missingPageBodies } from '@/features/order/insert';
import { findGaps } from '@/features/pages/gaps';

describe('missingPageBodies', () => {
  const book = (...labels: string[]) =>
    labels.map((label, index) => ({ id: `p${index}`, label, included: true }));

  it('asks for a missing page for each number the gap lacks, before the page the numbers lead to', () => {
    const gaps = findGaps(book('45', '46', '49'));
    expect(gaps.flatMap(missingPageBodies)).toEqual([
      { origin: 'placeholder', kind: 'text', label: '47', before_page_id: 'p2' },
      { origin: 'placeholder', kind: 'text', label: '48', before_page_id: 'p2' },
    ]);
  });

  it('writes the labels in the style of the numbers around the gap', () => {
    const bodies = findGaps(book('i', 'ii', 'v')).flatMap(missingPageBodies);
    expect(bodies.map((body) => body.label)).toEqual(['iii', 'iv']);
    const upper = findGaps(book('VIII', 'X')).flatMap(missingPageBodies);
    expect(upper.map((body) => body.label)).toEqual(['IX']);
  });

  it('asks for nothing when there is no gap', () => {
    expect(findGaps(book('1', '2', '3')).flatMap(missingPageBodies)).toEqual([]);
  });
});

describe('insertBody', () => {
  it('puts a page before the first selected page', () => {
    expect(insertBody({ origin: 'blank', place: 'before' }, ['b', 'c'])).toEqual({
      origin: 'blank',
      kind: 'blank',
      before_page_id: 'b',
    });
  });

  it('puts a page after the last selected page', () => {
    expect(insertBody({ origin: 'placeholder', place: 'after' }, ['b', 'c'])).toEqual({
      origin: 'placeholder',
      kind: 'text',
      after_page_id: 'c',
    });
  });

  it('names no place for the end of the book', () => {
    expect(insertBody({ origin: 'placeholder', place: 'end' }, ['b'])).toEqual({
      origin: 'placeholder',
      kind: 'text',
    });
  });

  it('falls back to the end of the book when nothing is selected', () => {
    expect(insertBody({ origin: 'blank', place: 'before' }, [])).toEqual({
      origin: 'blank',
      kind: 'blank',
    });
    expect(insertBody({ origin: 'blank', place: 'after' }, [])).toEqual({
      origin: 'blank',
      kind: 'blank',
    });
  });
});
