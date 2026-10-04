import { describe, expect, it } from 'vitest';
import {
  pageIdsFor,
  pagesOfCondition,
  RunScope,
  scopeChoices,
  troubleOf,
} from '@/features/processing/scope';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

const PAGES = [
  page('a', { position: 0 }),
  page('b', { position: 1 }),
  page('c', { position: 2 }),
  page('d', { position: 3 }),
  page('hole', { position: 4, origin: 'placeholder', images: null }),
];
const ITEMS = joinRows(PAGES, [
  row('a', { status: 'fresh' }),
  row('b', { status: 'stale' }),
  row('c', { status: 'failed' }),
  row('d', { status: 'not-run' }),
  row('hole', { status: 'stale' }),
]);

describe('pageIdsFor', () => {
  it('sends the open page alone for this page', () => {
    expect(pageIdsFor(RunScope.Page, ITEMS, 'b', new Set())).toEqual(['b']);
  });

  it('sends nothing for this page when no page is open', () => {
    expect(pageIdsFor(RunScope.Page, ITEMS, undefined, new Set())).toEqual([]);
  });

  it('sends the selected pages in the order of the book', () => {
    expect(pageIdsFor(RunScope.Selected, ITEMS, 'a', new Set(['d', 'b']))).toEqual(['b', 'd']);
  });

  it('sends the pages that are out of date or failed, and not the ones that are up to date or not yet run', () => {
    expect(pageIdsFor(RunScope.Attention, ITEMS, 'a', new Set())).toEqual(['b', 'c']);
  });

  it('sends no list for all pages, which the server reads as every page with an image', () => {
    expect(pageIdsFor(RunScope.All, ITEMS, 'a', new Set())).toBeNull();
  });

  it('never sends a placeholder, which has no image', () => {
    expect(pageIdsFor(RunScope.Selected, ITEMS, 'a', new Set(['hole', 'a']))).toEqual(['a']);
  });
});

describe('troubleOf', () => {
  it('counts the pages out of date and the pages that failed, without placeholders', () => {
    expect(troubleOf(ITEMS)).toEqual({ stale: 1, failed: 1 });
  });

  it('is zero while the rows of the stage are loading', () => {
    expect(troubleOf(joinRows(PAGES, []))).toEqual({ stale: 0, failed: 0 });
  });
});

describe('scopeChoices', () => {
  it('counts what each scope covers now, in the order of the menu', () => {
    expect(scopeChoices(ITEMS, 'a', new Set(['b', 'c', 'd']))).toEqual([
      { scope: 'page', count: 1 },
      { scope: 'selected', count: 3 },
      { scope: 'attention', count: 2 },
      { scope: 'all', count: 4 },
    ]);
  });
});

describe('pagesOfCondition', () => {
  const MIXED = joinRows(
    [
      page('text', { position: 0 }),
      page('cover', { position: 1, kind: 'cover' }),
      page('plate', { position: 2, kind: 'plate', content_type: 'color-picture' }),
      page('found', { position: 3, content_type: 'bw-picture', content_source: 'detected' }),
      page('set', { position: 4, kind: 'plate', content_type: 'text', content_source: 'hand' }),
      page('hole', {
        position: 5,
        kind: 'plate',
        content_type: 'color-picture',
        origin: 'placeholder',
        images: null,
      }),
    ],
    [],
  );
  const idsOf = (condition: Parameters<typeof pagesOfCondition>[1]): string[] =>
    pagesOfCondition(MIXED, condition).map((item) => item.page.id);

  it('lists every page that has an image for a step with no condition', () => {
    expect(idsOf('all')).toEqual(['text', 'cover', 'plate', 'found', 'set']);
  });

  it('lists the pages that show text, whatever their kind, for the pages of text', () => {
    expect(idsOf('text')).toEqual(['text', 'cover', 'set']);
  });

  it('lists the pages that show a picture for the pictures, and never a placeholder', () => {
    expect(idsOf('pictures')).toEqual(['plate', 'found']);
  });

  it('lists the pictures of one colour for the colour conditions', () => {
    expect(idsOf('color-pictures')).toEqual(['plate']);
    expect(idsOf('bw-pictures')).toEqual(['found']);
  });
});
