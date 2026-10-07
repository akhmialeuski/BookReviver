import { describe, expect, it } from 'vitest';
import {
  clampDivider,
  clipWidth,
  comparePairOf,
  innerRect,
  placementOf,
  Side,
  SourceKind,
  sameImage,
  sourceOfPreview,
  sourceOfResult,
} from '@/features/processing/compare';
import { version } from '@/features/processing/fixtures';
import { row, stepPage } from '@/features/workspace/fixtures';

describe('sourceOfResult', () => {
  it('draws a result from its pyramid once the tiles are cut', () => {
    expect(sourceOfResult(version('v', { tiles_ready: true }))).toEqual({
      kind: SourceKind.Iiif,
      url: '/version-v/info.json',
    });
  });

  it('draws a result whose tiles are not cut from its preview image', () => {
    expect(sourceOfResult(version('v', { tiles_ready: false }))).toEqual({
      kind: SourceKind.Image,
      url: '/version-v/preview',
    });
  });

  it('is nothing for a result with no image, or no result at all', () => {
    expect(sourceOfResult(version('v', { images: null }))).toBeNull();
    expect(sourceOfResult(null)).toBeNull();
    expect(sourceOfResult(undefined)).toBeNull();
  });
});

describe('sourceOfPreview', () => {
  it('is the plain image of a preview', () => {
    expect(sourceOfPreview(version('v', { preview: '/previews/v.png' }))).toEqual({
      kind: SourceKind.Image,
      url: '/previews/v.png',
    });
  });

  it('is nothing for a version with no preview image', () => {
    expect(sourceOfPreview(version('v', { preview: null }))).toBeNull();
  });
});

describe('comparePairOf', () => {
  const read = version('read');
  const made = version('made');
  const head = version('head');

  it('draws the picture of the page before and what the open step made after', () => {
    const pair = comparePairOf(
      read,
      row('a', { version: head, picture: read, step: stepPage('s', 'found', { version: made }) }),
      null,
    );

    expect(pair.before?.url).toBe('/version-read/info.json');
    expect(pair.after?.url).toBe('/version-made/info.json');
  });

  it('draws nothing after for a step the page has not been run through, not the result of the stage', () => {
    const pair = comparePairOf(
      read,
      row('a', { version: head, picture: read, step: stepPage('s', 'default', { version: null }) }),
      null,
    );

    expect(pair.before?.url).toBe('/version-read/info.json');
    expect(pair.after).toBeNull();
  });

  it('draws the result of the stage after when no step is open', () => {
    const pair = comparePairOf(head, row('a', { version: head, picture: head }), null);

    expect(pair.after?.url).toBe('/version-head/info.json');
  });

  it('draws a preview that is on in place of what the step made', () => {
    const pair = comparePairOf(
      read,
      row('a', { picture: read, step: stepPage('s', 'found', { version: made }) }),
      version('shown', { preview: '/previews/shown.png' }),
    );

    expect(pair.after).toEqual({ kind: SourceKind.Image, url: '/previews/shown.png' });
  });

  it('draws nothing before while the row loads, never the image of the page', () => {
    expect(comparePairOf(null, undefined, null)).toEqual({ before: null, after: null });
  });
});

describe('sameImage', () => {
  it('is true for one picture and false for two, or for none', () => {
    const a = sourceOfResult(version('a'));
    const b = sourceOfResult(version('b'));

    expect(sameImage(a, sourceOfResult(version('a')))).toBe(true);
    expect(sameImage(a, b)).toBe(false);
    expect(sameImage(a, null)).toBe(false);
    expect(sameImage(null, null)).toBe(false);
  });
});

describe('clipWidth', () => {
  it('uncovers the part of the picture left of the divider', () => {
    expect(clipWidth(0.5, 0, 1, 2000)).toBe(1000);
    expect(clipWidth(0.25, 0, 1, 2000)).toBe(500);
  });

  it('follows the picture when it is panned', () => {
    // The picture starts at 0.2 and is 0.4 wide, so the divider at 0.4 is half way across it
    expect(clipWidth(0.4, 0.2, 0.4, 1000)).toBeCloseTo(500);
  });

  it('shows nothing when the divider is left of the picture and all of it when it is right of it', () => {
    expect(clipWidth(0.1, 0.2, 0.4, 1000)).toBe(0);
    expect(clipWidth(0.9, 0.2, 0.4, 1000)).toBe(1000);
  });

  it('shows nothing of a picture with no width', () => {
    expect(clipWidth(0.5, 0.5, 0, 1000)).toBe(0);
  });
});

describe('clampDivider', () => {
  it('keeps the divider on the canvas', () => {
    expect(clampDivider(-1)).toBe(0.02);
    expect(clampDivider(2)).toBe(0.98);
    expect(clampDivider(0.4)).toBe(0.4);
  });
});

describe('placementOf', () => {
  const none = { quad: null, angle: null, mesh_key: null };
  // A block of 1276 by 2645 pixels that the margins put on a page of 1600 by 2800, 162 pixels from the left and 60 from the top
  const block = version('block', { data: { width_px: 1276, height_px: 2645 } });
  const margins = (overrides: Partial<Parameters<typeof version>[1]> = {}) =>
    version('page', {
      transform: { kind: 'place', ...none, matrix: [1, 0, 162, 0, 1, 60, 0, 0, 1] },
      data: { width_px: 1600, height_px: 2800, source_width_px: 1600, source_height_px: 2800 },
      ...overrides,
    });

  it('draws the page of the margins whole, and the block inside it where the matrix puts it', () => {
    const placement = placementOf(margins(), block);

    expect(placement?.base).toBe(Side.After);
    expect(placement?.left).toBeCloseTo(162 / 1600);
    expect(placement?.top).toBeCloseTo(60 / 2800);
    expect(placement?.width).toBeCloseTo(1276 / 1600);
    expect(placement?.height).toBeCloseTo(2645 / 2800);
  });

  it('follows the scale of the matrix, so a block that is shrunk lies smaller on the page', () => {
    const shrunk = margins({
      transform: { kind: 'place', ...none, matrix: [0.5, 0, 100, 0, 0.5, 200, 0, 0, 1] },
    });
    const placement = placementOf(shrunk, block);

    expect(placement?.width).toBeCloseTo(638 / 1600);
    expect(placement?.height).toBeCloseTo(1322.5 / 2800);
    expect(placement?.top).toBeCloseTo(200 / 2800);
  });

  it('counts a preview of the margins in the pixels of the shrunk page, which the size of the full page gives', () => {
    const preview = margins({
      scale: 'preview',
      transform: { kind: 'place', ...none, matrix: [1, 0, 81, 0, 1, 30, 0, 0, 1] },
      data: { width_px: 800, height_px: 1400, source_width_px: 1600, source_height_px: 2800 },
    });
    const placement = placementOf(preview, block);

    expect(placement?.left).toBeCloseTo(81 / 800);
    expect(placement?.width).toBeCloseTo((1276 * 0.5) / 800);
    expect(placement?.height).toBeCloseTo((2645 * 0.5) / 1400);
  });

  it('draws the input whole and the result inside it for a page that an old crop cut out', () => {
    // Cut at 237 from the left and 150 from the top, from a page of 1695 by 2795 pixels
    const cut = version('cut', {
      transform: { kind: 'crop', ...none, matrix: [1, 0, -237, 0, 1, -150, 0, 0, 1] },
      data: { width_px: 1277, height_px: 2645 },
    });
    const placement = placementOf(
      cut,
      version('page', { data: { width_px: 1695, height_px: 2795 } }),
    );

    expect(placement?.base).toBe(Side.Before);
    expect(placement?.left).toBeCloseTo(237 / 1695);
    expect(placement?.top).toBeCloseTo(150 / 2795);
    expect(placement?.width).toBeCloseTo(1277 / 1695);
    expect(placement?.height).toBeCloseTo(2645 / 2795);
  });

  it('gives no place to a result of the size of its input, so both are drawn as tall as each other', () => {
    const same = version('same', {
      transform: { kind: 'crop', ...none, matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1] },
      data: { width_px: 1695, height_px: 2795 },
    });

    expect(
      placementOf(same, version('page', { data: { width_px: 1695, height_px: 2795 } })),
    ).toBeNull();
    expect(
      placementOf(
        version('identity', { transform: { kind: 'identity', ...none, matrix: null } }),
        block,
      ),
    ).toBeNull();
  });

  it('gives no place to a turn, a bend or a version without what the matrix needs', () => {
    const turned = version('turned', {
      transform: { kind: 'rotate', ...none, angle: 1.5, matrix: [1, 0, 0, 0, 1, 0, 0, 0, 1] },
      data: { width_px: 1600, height_px: 2800 },
    });
    const sheared = margins({
      transform: { kind: 'place', ...none, matrix: [1, 0.2, 162, 0, 1, 60, 0, 0, 1] },
    });

    expect(placementOf(turned, block)).toBeNull();
    expect(placementOf(sheared, block)).toBeNull();
    expect(placementOf(margins(), version('blank'))).toBeNull();
    expect(placementOf(margins(), null)).toBeNull();
    expect(placementOf(null, block)).toBeNull();
    // A preview of a step that does not record the size of the full page has no ratio to count by
    expect(
      placementOf(margins({ scale: 'preview', data: { width_px: 800, height_px: 1400 } }), block),
    ).toBeNull();
  });
});

describe('innerRect', () => {
  it('turns the shares of the base picture into a rectangle of the world', () => {
    const rect = innerRect(
      { base: Side.After, left: 0.1, top: 0.05, width: 0.8, height: 0.9 },
      { x: 0, y: 0, width: 0.6, height: 1 },
    );

    expect(rect.x).toBeCloseTo(0.06);
    expect(rect.y).toBeCloseTo(0.05);
    expect(rect.width).toBeCloseTo(0.48);
    expect(rect.height).toBeCloseTo(0.9);
  });
});
