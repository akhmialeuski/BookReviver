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
 * What each section covers, read from the section every page names. The server decides which section governs a page, so
 * these cases give the pages their `section_id` and check only the grouping and what a row of the panel shows.
 */

/** A book of pages `p0`, `p1` and so on, each with the label and the section id the table gives it. */
function book(rows: readonly (readonly [string, string | null])[]): PageSchema[] {
  return rows.map(([label, sectionId], position) =>
    page(`p${position}`, { position, label, section_id: sectionId }),
  );
}

describe('sectionSpans', () => {
  it('gives each section the pages that name it, in the order the sections are given', () => {
    const pages = book([
      ['', 's1'],
      ['', 's1'],
      ['[i]', 's2'],
      ['[ii]', 's2'],
      ['1', 's3'],
      ['2', 's3'],
    ]);
    const spans = sectionSpans(pages, [
      section('s1', 'p0', { display: 'not-counted' }),
      section('s2', 'p2', { display: 'counted', style: 'roman-lower' }),
      section('s3', 'p4'),
    ]);

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
    const pages = book([
      ['', 's1'],
      ['[i]', 's2'],
      ['[ii]', 's2'],
      ['1', 's3'],
    ]);
    const spans = sectionSpans(pages, [
      section('s1', 'p0', { display: 'not-counted' }),
      section('s2', 'p1', { display: 'counted' }),
      section('s3', 'p3'),
    ]);

    expect(spans.map((span) => span.labels)).toEqual([
      null,
      { first: '[i]', last: '[ii]' },
      { first: '1', last: '1' },
    ]);
  });

  it('colours counted sections in turn and a section that is not counted grey, without using up a colour', () => {
    const pages = book([
      ['', 's1'],
      ['i', 's2'],
      ['1', 's3'],
    ]);
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
    const rows = Array.from(
      { length: SECTION_TONES.length + 1 },
      (_, index) => ['1', `s${index}`] as const,
    );
    const pages = book(rows);
    const spans = sectionSpans(
      pages,
      pages.map((entry) => section(`${entry.section_id}`, entry.id)),
    );

    expect(spans.at(-1)?.tone).toBe(SECTION_TONES[0]);
  });

  it('shows the place of a series from its first page to its last page, with the pages of the others between', () => {
    const pages = book([
      ['1', 'text'],
      ['Plate I', 'plates'],
      ['2', 'text'],
      ['Plate II', 'plates'],
      ['3', 'text'],
    ]);
    const [text, plates] = sectionSpans(pages, [
      section('text', 'p0'),
      section('plates', 'p0', { kinds: ['plate'] }),
    ]);

    expect(text?.pages.map((entry) => entry.id)).toEqual(['p0', 'p2', 'p4']);
    expect(plates?.pages.map((entry) => entry.id)).toEqual(['p1', 'p3']);
    expect(plates?.labels).toEqual({ first: 'Plate I', last: 'Plate II' });
    expect([plates?.firstPosition, plates?.lastPosition]).toEqual([1, 4]);
  });

  it('gives a section that no page names its own first page as its place', () => {
    const pages = book([
      ['1', 's1'],
      ['2', 's1'],
    ]);
    const [only] = sectionSpans(pages, [section('plates', 'p1', { kinds: ['plate'] })]);

    expect(only?.pages).toEqual([]);
    expect([only?.firstPosition, only?.lastPosition]).toEqual([2, 2]);
    expect(only?.labels).toBeNull();
  });
});

describe('spanOfPages', () => {
  it('finds the section of a page by its id, and none for a page that names no section', () => {
    const pages = book([
      ['x', null],
      ['1', 'text'],
    ]);
    const lookup = spanOfPages(sectionSpans(pages, [section('text', 'p1')]));

    expect(lookup.has('p0')).toBe(false);
    expect(lookup.get('p1')?.section.id).toBe('text');
  });
});

describe('sectionName', () => {
  it('names a section by its name, or by its place among the sections when it has none', () => {
    const pages = book([
      ['i', 's1'],
      ['1', 's2'],
    ]);
    const [first, second] = sectionSpans(pages, [
      section('s1', 'p0', { name: 'Preface' }),
      section('s2', 'p1'),
    ]);

    expect(first && sectionName(first)).toBe('Preface');
    expect(second && sectionName(second)).toBe('Section 2');
  });
});
