import { describe, expect, it } from 'vitest';
import { formatRuns, pageRunsOf, resolutionOf } from '@/features/import/facts';
import { scan } from '@/features/import/fixtures';
import { page } from '@/features/workspace/fixtures';

describe('pageRunsOf', () => {
  it('counts the places from 1 and joins neighbours into one run', () => {
    const pages = [0, 1, 2].map((position) =>
      page(`p-${position}`, { position, source_id: 'f-1' }),
    );
    expect(pageRunsOf(pages, 'f-1')).toEqual([{ first: 1, last: 3 }]);
  });

  it('splits the runs where another file stands between the pages', () => {
    const pages = [
      page('a', { position: 0, source_id: 'f-1' }),
      page('b', { position: 1, source_id: 'f-2' }),
      page('c', { position: 2, source_id: 'f-1' }),
      page('d', { position: 3, source_id: 'f-1' }),
    ];
    expect(pageRunsOf(pages, 'f-1')).toEqual([
      { first: 1, last: 1 },
      { first: 3, last: 4 },
    ]);
  });

  it('puts the places in book order whatever order the pages are listed in', () => {
    const pages = [
      page('late', { position: 5, source_id: 'f-1' }),
      page('early', { position: 4, source_id: 'f-1' }),
    ];
    expect(pageRunsOf(pages, 'f-1')).toEqual([{ first: 5, last: 6 }]);
  });

  it('finds nothing for a file no page was cut from', () => {
    expect(pageRunsOf([page('a', { source_id: 'f-2' })], 'f-1')).toEqual([]);
    expect(pageRunsOf([], 'f-1')).toEqual([]);
  });
});

describe('formatRuns', () => {
  it('writes a run with an en dash and a single place alone', () => {
    expect(
      formatRuns([
        { first: 1, last: 40 },
        { first: 55, last: 55 },
      ]),
    ).toBe('1–40, 55');
  });

  it('writes nothing for no runs', () => {
    expect(formatRuns([])).toBe('');
  });
});

describe('resolutionOf', () => {
  function withDpi(id: string, dpi: number | null) {
    const base = scan(id);
    return { ...base, facts: { ...base.facts, dpi_x: dpi } };
  }

  it('is one value when the scans agree', () => {
    expect(resolutionOf([withDpi('a', 400), withDpi('b', 400)])).toEqual({ min: 400, max: 400 });
  });

  it('is the range when the scans differ, rounded to whole dots', () => {
    expect(resolutionOf([withDpi('a', 299.6), withDpi('b', 600)])).toEqual({ min: 300, max: 600 });
  });

  it('ignores a scan that reports no resolution', () => {
    expect(resolutionOf([withDpi('a', null), withDpi('b', 300)])).toEqual({ min: 300, max: 300 });
  });

  it('is nothing when no scan reports one', () => {
    expect(resolutionOf([withDpi('a', null)])).toBeNull();
    expect(resolutionOf([])).toBeNull();
  });
});
