import { describe, expect, it } from 'vitest';
import { parseViewerSearch } from '@/features/viewer/params';

describe('parseViewerSearch', () => {
  it('reads a page id and a spread flag', () => {
    expect(parseViewerSearch({ page: 'a1b2-c3', spread: true })).toEqual({
      page: 'a1b2-c3',
      spread: true,
    });
  });

  it('gives an empty state for an empty address', () => {
    expect(parseViewerSearch({})).toEqual({});
  });

  it.each([true, 1, 'true', '1'])('turns the spread on for %j', (spread) => {
    expect(parseViewerSearch({ spread })).toEqual({ spread: true });
  });

  it.each([false, 0, '0', 'false', 'yes', 2, null, {}, []])(
    'leaves the spread off for %j',
    (spread) => {
      expect(parseViewerSearch({ spread })).toEqual({});
    },
  );

  it('keeps a page id that the router decoded as a number', () => {
    expect(parseViewerSearch({ page: 12345 })).toEqual({ page: '12345' });
  });

  it('trims the page id', () => {
    expect(parseViewerSearch({ page: '  abc  ' })).toEqual({ page: 'abc' });
  });

  it.each(['', '   ', null, {}, [], true])('drops the page %j', (page) => {
    expect(parseViewerSearch({ page })).toEqual({});
  });

  it('ignores parameters the viewer does not own', () => {
    expect(parseViewerSearch({ page: 'p', extra: 'x' })).toEqual({ page: 'p' });
  });
});
