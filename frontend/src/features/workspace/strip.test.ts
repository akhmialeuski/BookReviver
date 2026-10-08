import { describe, expect, it } from 'vitest';
import type { PageVersionSchema } from '@/api';
import { images, page, row, stepPage } from '@/features/workspace/fixtures';
import { PageFilter } from '@/features/workspace/params';
import {
  applyFilter,
  applyFlag,
  applyStopped,
  canvasSourceOf,
  countFilters,
  flagOptions,
  flagsOf,
  isLeftOut,
  isMarkedBad,
  joinRows,
  needsCheck,
  pictureOf,
  stopOptions,
  stripRowsOf,
  thumbnailOf,
  thumbnailsOf,
} from '@/features/workspace/strip';

describe('joinRows', () => {
  it('keeps the order of the book and pairs each page with its row', () => {
    const items = joinRows(
      [page('a'), page('b'), page('c')],
      [row('c'), row('a', { status: 'stale' })],
    );
    expect(items.map((item) => item.page.id)).toEqual(['a', 'b', 'c']);
    expect(items.map((item) => item.row?.status)).toEqual(['stale', undefined, 'fresh']);
  });

  it('is empty for a book without pages, whatever rows it is given', () => {
    expect(joinRows([], [row('a')])).toEqual([]);
  });
});

describe('the flags of the pages at a step', () => {
  const rows = [
    row('a', { step: stepPage('s', 'found', { flags: ['unsure', 'by-hand'] }) }),
    row('b', { step: stepPage('s', 'skipped', { flags: ['skipped'] }) }),
    row('c', { step: stepPage('s', 'found') }),
    row('d', { step: stepPage('s', 'found', { flags: ['unusual'] }) }),
    row('e'),
  ];
  const items = joinRows([page('a'), page('b'), page('c'), page('d'), page('e')], rows);

  it('reads the flags the server put on each row, and leaves out a row asked for no step', () => {
    expect([...flagsOf(rows)]).toEqual([
      ['a', ['unsure', 'by-hand']],
      ['b', ['skipped']],
      ['c', []],
      ['d', ['unusual']],
    ]);
  });

  it('lists every flag in a fixed order with the pages that carry it', () => {
    expect(flagOptions(flagsOf(rows))).toEqual([
      { flag: 'unsure', pages: 1 },
      { flag: 'unusual', pages: 1 },
      { flag: 'by-hand', pages: 1 },
      { flag: 'skipped', pages: 1 },
    ]);
  });

  it('lists every flag with no pages for a book none of whose pages carry one', () => {
    expect(flagOptions(new Map()).map((option) => option.pages)).toEqual([0, 0, 0, 0]);
  });

  it('keeps the pages that carry the flag, and every page for no flag', () => {
    const flags = flagsOf(rows);

    expect(applyFlag(items, flags, 'by-hand').map((item) => item.page.id)).toEqual(['a']);
    expect(applyFlag(items, flags, 'skipped').map((item) => item.page.id)).toEqual(['b']);
    expect(applyFlag(items, flags, null)).toBe(items);
  });

  it('keeps no page for a flag that no page carries', () => {
    expect(applyFlag(items, new Map(), 'unsure')).toEqual([]);
  });
});

describe('steps a run stopped at', () => {
  const items = joinRows(
    [page('a'), page('b'), page('c'), page('d'), page('e')],
    [
      row('a', { through_step: 1 }),
      row('b', { through_step: 0 }),
      row('c', { through_step: 1 }),
      row('d', { status: 'failed', through_step: 2 }),
      row('e'),
    ],
  );

  it('lists the steps with the pages of each, the first step first, and leaves out failed pages', () => {
    expect(stopOptions(items)).toEqual([
      { step: 0, pages: 1 },
      { step: 1, pages: 2 },
    ]);
  });

  it('lists none for a stage no run stopped short', () => {
    expect(stopOptions(joinRows([page('a')], [row('a')]))).toEqual([]);
  });

  it('keeps the pages that stopped at a step, and every page for no step', () => {
    expect(applyStopped(items, 1).map((item) => item.page.id)).toEqual(['a', 'c']);
    expect(applyStopped(items, null)).toHaveLength(5);
  });
});

describe('needsCheck', () => {
  it('asks for a look at a page that is out of date, failed or marked', () => {
    expect(needsCheck({ page: page('a'), row: row('a', { status: 'stale' }) })).toBe(true);
    expect(needsCheck({ page: page('a'), row: row('a', { status: 'failed' }) })).toBe(true);
    expect(needsCheck({ page: page('a'), row: row('a', { review: 'low-confidence' }) })).toBe(true);
  });

  it('does not ask for a look at a page that is up to date or was never processed', () => {
    expect(needsCheck({ page: page('a'), row: row('a') })).toBe(false);
    expect(needsCheck({ page: page('a'), row: row('a', { status: 'not-run' }) })).toBe(false);
  });

  it('does not ask for a look while the rows are loading', () => {
    expect(needsCheck({ page: page('a'), row: undefined })).toBe(false);
  });
});

describe('filters', () => {
  const items = joinRows(
    [page('a'), page('b'), page('c', { included: false }), page('d')],
    [
      row('a'),
      row('b', { status: 'failed' }),
      row('c', { status: 'stale', marked_bad: true }),
      row('d', { review: 'not-applied' }),
    ],
  );

  it('lists every page for All', () => {
    expect(applyFilter(items, PageFilter.All)).toHaveLength(4);
  });

  it('lists the pages to check for Check, a page left out of the book included', () => {
    expect(applyFilter(items, PageFilter.Check).map((item) => item.page.id)).toEqual([
      'b',
      'c',
      'd',
    ]);
  });

  it('lists the pages whose result is marked bad for Marked bad, whatever else they are', () => {
    expect(applyFilter(items, PageFilter.Bad).map((item) => item.page.id)).toEqual(['c']);
    expect(items.map(isMarkedBad)).toEqual([false, false, true, false]);
  });

  it('marks no page while the rows are loading', () => {
    expect(isMarkedBad({ page: page('a'), row: undefined })).toBe(false);
  });

  it('lists the pages kept out of the book for Left out', () => {
    expect(applyFilter(items, PageFilter.LeftOut).map((item) => item.page.id)).toEqual(['c']);
    expect(items.filter(isLeftOut).map((item) => item.page.id)).toEqual(['c']);
  });

  it('lists the pages cut from a wide scan for Wide', () => {
    const cut = joinRows(
      [page('a', { scan_id: 's1' }), page('b', { scan_id: 's2' }), page('c', { scan_id: null })],
      [],
      new Set(['s1']),
    );

    expect(applyFilter(cut, PageFilter.Wide).map((item) => item.page.id)).toEqual(['a']);
    expect(countFilters(cut)[PageFilter.Wide]).toBe(1);
  });

  it('lists no page for Wide when no scan is known to be wide', () => {
    expect(applyFilter(items, PageFilter.Wide)).toEqual([]);
  });

  it('counts what each filter lists', () => {
    expect(countFilters(items)).toEqual({ all: 4, check: 3, bad: 1, 'left-out': 1, wide: 0 });
  });
});

describe('the picture of a page', () => {
  const picture = { images: images('picture'), tiles_ready: true } as PageVersionSchema;
  const head = { images: images('head'), tiles_ready: true } as PageVersionSchema;
  const made = { images: images('made'), tiles_ready: true } as PageVersionSchema;

  it('is the picture of the row on the strip and on the canvas alike', () => {
    const item = { page: page('a'), row: row('a', { picture }) };

    expect(pictureOf(item)).toBe(picture);
    expect(thumbnailOf(item)).toBe('/picture/thumb');
    expect(canvasSourceOf(item)).toBe('/picture/info.json');
  });

  it('is not the result of the stage nor what the step made when the picture differs from them', () => {
    const item = {
      page: page('a'),
      row: row('a', { version: head, picture, step: stepPage('s', 'found', { version: made }) }),
    };

    expect(thumbnailOf(item)).toBe('/picture/thumb');
    expect(canvasSourceOf(item)).toBe('/picture/info.json');
  });

  it('is nothing while the row loads, never the image of the page', () => {
    const item = { page: page('a'), row: undefined };

    expect(thumbnailOf(item)).toBeNull();
    expect(canvasSourceOf(item)).toBeNull();
  });

  it('is nothing for a row with no picture, never the image of the page', () => {
    const item = { page: page('a'), row: row('a', { version: head }) };

    expect(thumbnailOf(item)).toBeNull();
    expect(canvasSourceOf(item)).toBeNull();
  });

  it('is drawn on the canvas from a pyramid only once its tiles are cut', () => {
    const uncut = { ...picture, tiles_ready: false } as PageVersionSchema;
    const item = { page: page('a'), row: row('a', { picture: uncut }) };

    expect(thumbnailOf(item)).toBe('/picture/thumb');
    expect(canvasSourceOf(item)).toBeNull();
  });
});

describe('the thumbnails of a grid of pages', () => {
  const picture = { images: images('picture'), tiles_ready: true } as PageVersionSchema;

  it('are the thumbnails of the pictures of the rows by page, never the images of the pages', () => {
    const items = joinRows(
      [page('a', { images: images('latest') }), page('b'), page('c')],
      [row('a', { picture })],
    );

    expect(thumbnailsOf(items)).toEqual(new Map([['a', '/picture/thumb']]));
  });
});

describe('the rows of the strip', () => {
  const stepRows = [row('a', { picture: null })];
  const stageRows = [row('a')];

  it('are none while the rows of the open step load, never the rows of the stage', () => {
    expect(stripRowsOf(true, undefined, stageRows)).toBeUndefined();
  });

  it('are the rows of the open step once they are read', () => {
    expect(stripRowsOf(true, stepRows, stageRows)).toBe(stepRows);
  });

  it('are the rows of the stage when the stage has no bar', () => {
    expect(stripRowsOf(false, undefined, stageRows)).toBe(stageRows);
    expect(stripRowsOf(false, undefined, undefined)).toBeUndefined();
  });
});
