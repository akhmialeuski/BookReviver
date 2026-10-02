import { describe, expect, it } from 'vitest';
import {
  clampStart,
  DEFAULT_SKIPPED_KINDS,
  endOfRunBefore,
  type NumberingDraft,
  newDraft,
  numberingBody,
  pagesInRange,
} from '@/features/order/numbering';
import { page } from '@/features/workspace/fixtures';

const BOOK = ['a', 'b', 'c', 'd', 'e'].map((id, index) => page(id, { position: index }));

describe('newDraft', () => {
  it('numbers from the page given to the end of the book, in Arabic from 1', () => {
    expect(newDraft(BOOK, 'c')).toEqual({
      firstId: 'c',
      lastId: 'e',
      style: 'arabic',
      start: 1,
      bracketed: false,
      skipKinds: DEFAULT_SKIPPED_KINDS,
    });
  });

  it('numbers the whole book when no page is given or the page is not in the book', () => {
    expect(newDraft(BOOK)).toMatchObject({ firstId: 'a', lastId: 'e' });
    expect(newDraft(BOOK, 'zz')).toMatchObject({ firstId: 'a', lastId: 'e' });
  });

  it('gives nothing for a book without pages', () => {
    expect(newDraft([])).toBeNull();
  });
});

describe('clampStart', () => {
  it('keeps a number from 1 and drops its fraction', () => {
    expect(clampStart('arabic', 12.9)).toBe(12);
    expect(clampStart('arabic', 0)).toBe(1);
    expect(clampStart('arabic', -4)).toBe(1);
    expect(clampStart('arabic', Number.NaN)).toBe(1);
  });

  it('stops a Roman numeral where it cannot be written any more', () => {
    expect(clampStart('roman-lower', 5000)).toBe(3999);
    expect(clampStart('roman-upper', 3999)).toBe(3999);
    expect(clampStart('arabic', 5000)).toBe(5000);
  });
});

function draftFrom(firstId: string): NumberingDraft {
  const draft = newDraft(BOOK, firstId);
  if (draft === null) {
    throw new Error('The book of the test has pages');
  }
  return draft;
}

describe('numberingBody', () => {
  it('writes the draft as the body of the server', () => {
    expect(numberingBody({ ...draftFrom('b'), start: 0, bracketed: true })).toEqual({
      first_page_id: 'b',
      last_page_id: 'e',
      style: 'arabic',
      start: 1,
      bracketed: true,
      skip_kinds: [...DEFAULT_SKIPPED_KINDS],
    });
  });
});

describe('pagesInRange', () => {
  const draft = { ...draftFrom('b'), lastId: 'd' };

  it('lists the pages from the first to the last, both ends included', () => {
    expect(pagesInRange(BOOK, draft)?.map((entry) => entry.id)).toEqual(['b', 'c', 'd']);
  });

  it('gives nothing for a range that runs backwards or leaves the book', () => {
    expect(pagesInRange(BOOK, { ...draft, firstId: 'd', lastId: 'b' })).toBeNull();
    expect(pagesInRange(BOOK, { ...draft, lastId: 'zz' })).toBeNull();
    expect(pagesInRange(BOOK, { ...draft, firstId: 'zz' })).toBeNull();
  });

  it('takes one page when the ends are the same', () => {
    expect(pagesInRange(BOOK, { ...draft, firstId: 'c', lastId: 'c' })).toHaveLength(1);
  });
});

describe('endOfRunBefore', () => {
  it('gives the page just before the one the numbers lead to', () => {
    expect(endOfRunBefore(BOOK, 'c')?.id).toBe('b');
  });

  it('gives nothing for the first page or a page outside the book', () => {
    expect(endOfRunBefore(BOOK, 'a')).toBeNull();
    expect(endOfRunBefore(BOOK, 'zz')).toBeNull();
  });
});
