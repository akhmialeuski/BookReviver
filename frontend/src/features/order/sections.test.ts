import { describe, expect, it } from 'vitest';
import type { PageSchema } from '@/api';
import {
  NOT_COUNTED_TONE,
  SECTION_TONES,
  sectionName,
  sectionSpans,
  spanOfPages,
} from '@/features/order/sections';
import { page, section } from '@/features/workspace/fixtures';

/**
 * Which section each page of the book belongs to, and what each section covers, read the way the server numbers the
 * pages: the main flow runs section to section, and a series by kind takes the pages of its kinds across the book.
 */

/** A book of pages `p0`, `p1` and so on, each with the label and kind the table gives it. */
function book(rows: readonly (readonly [string, PageSchema['kind']?])[]): PageSchema[] {
  return rows.map(([label, kind], position) =>
    page(`p${position}`, { position, label, kind: kind ?? 'text' }),
  );
}

describe('sectionSpans', () => {
  it('gives a section the pages from its first page to the page before the next section', () => {
    const pages = book([['', 'cover'], [''], ['[i]'], ['[ii]'], ['1'], ['2']]);
    const spans = sectionSpans(pages, [
      section('s3', 'p4'),
      section('s1', 'p0', { display: 'not-counted' }),
      section('s2', 'p2', { display: 'counted', style: 'roman-lower' }),
    ]);

    expect(spans.map((span) => span.section.id)).toEqual(['s1', 's2', 's3']);
    expect(spans.map((span) => span.pages.map((entry) => entry.id))).toEqual([
      ['p0', 'p1'],
      ['p2', 'p3'],
      ['p4', 'p5'],
    ]);
    expect(spans.map((span) => [span.firstPosition, span.lastPosition])).toEqual([
      [1, 2],
      [3, 4],
      [5, 6],
    ]);
    expect(spans.map((span) => span.ordinal)).toEqual([1, 2, 3]);
  });

  it('reads the first and the last number a section shows, and none for a section without numbers', () => {
    const pages = book([[''], [''], ['[i]'], ['[ii]'], ['1'], ['2']]);
    const spans = sectionSpans(pages, [
      section('s1', 'p0', { display: 'not-counted' }),
      section('s2', 'p2', { display: 'counted' }),
      section('s3', 'p4'),
    ]);

    expect(spans.map((span) => span.labels)).toEqual([
      null,
      { first: '[i]', last: '[ii]' },
      { first: '1', last: '2' },
    ]);
  });

  it('colours counted sections in turn and a section that is not counted grey, without using up a colour', () => {
    const pages = book([[''], ['i'], ['1'], ['2']]);
    const spans = sectionSpans(pages, [
      section('s1', 'p0', { display: 'not-counted' }),
      section('s2', 'p1'),
      section('s3', 'p2'),
    ]);

    expect(spans.map((span) => span.tone)).toEqual([
      NOT_COUNTED_TONE,
      SECTION_TONES[0],
      SECTION_TONES[1],
    ]);
  });

  it('starts the colours over when the book has more counted sections than colours', () => {
    const count = SECTION_TONES.length + 1;
    const pages = book(Array.from({ length: count }, () => ['1'] as const));
    const spans = sectionSpans(
      pages,
      pages.map((entry) => section(`s-${entry.id}`, entry.id)),
    );

    expect(spans.at(-1)?.tone).toBe(SECTION_TONES[0]);
  });

  it('keeps the pages of a series out of the main flow, which goes on counting without them', () => {
    const pages = book([['1'], ['Plate I', 'plate'], ['2'], ['Plate II', 'plate'], ['3']]);
    const spans = sectionSpans(pages, [
      section('text', 'p0'),
      section('plates', 'p0', { kinds: ['plate'], style: 'roman-upper', prefix: 'Plate ' }),
    ]);

    const [flow, plates] = spans;
    expect(flow?.pages.map((entry) => entry.id)).toEqual(['p0', 'p2', 'p4']);
    expect(plates?.pages.map((entry) => entry.id)).toEqual(['p1', 'p3']);
    expect(plates?.labels).toEqual({ first: 'Plate I', last: 'Plate II' });
    expect([plates?.firstPosition, plates?.lastPosition]).toEqual([1, 4]);
  });

  it('puts the main flow ahead of a series that starts on the same page', () => {
    const pages = book([['1'], ['2']]);
    const spans = sectionSpans(pages, [
      section('plates', 'p0', { kinds: ['plate'] }),
      section('text', 'p0'),
    ]);

    expect(spans.map((span) => span.section.id)).toEqual(['text', 'plates']);
  });

  it('leaves out a section whose first page is not in the book, and the pages before the first section', () => {
    const pages = book([['x'], ['1'], ['2']]);
    const spans = sectionSpans(pages, [section('lost', 'gone'), section('text', 'p1')]);

    expect(spans.map((span) => span.section.id)).toEqual(['text']);
    expect(spanOfPages(spans).has('p0')).toBe(false);
    expect(spanOfPages(spans).get('p2')?.section.id).toBe('text');
  });

  it('gives a section that takes no page its own first page as its place', () => {
    const pages = book([['1'], ['2']]);
    const [only] = sectionSpans(pages, [section('plates', 'p1', { kinds: ['plate'] })]);

    expect(only?.pages).toEqual([]);
    expect([only?.firstPosition, only?.lastPosition]).toEqual([2, 2]);
    expect(only?.labels).toBeNull();
  });
});

describe('sectionName', () => {
  it('names a section by its name, or by its place among the sections when it has none', () => {
    const pages = book([['i'], ['1']]);
    const [first, second] = sectionSpans(pages, [
      section('s1', 'p0', { name: 'Preface' }),
      section('s2', 'p1'),
    ]);

    expect(first && sectionName(first)).toBe('Preface');
    expect(second && sectionName(second)).toBe('Section 2');
  });
});
