import { describe, expect, it } from 'vitest';
import {
  beforeSourceOf,
  clampDivider,
  clipWidth,
  SourceKind,
  sourceOfPreview,
  sourceOfResult,
} from '@/features/processing/compare';
import { version } from '@/features/processing/fixtures';
import { page, row } from '@/features/workspace/fixtures';

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

describe('beforeSourceOf', () => {
  const subject = page('a');
  const earlier = new Map([['a', row('a', { version: version('split') })]]);

  it('is the result of the nearest earlier stage that has one', () => {
    const nearest = new Map([['a', row('a', { version: version('near') })]]);

    expect(beforeSourceOf(subject, undefined, [nearest, earlier])?.url).toBe(
      '/version-near/info.json',
    );
  });

  it('goes on to a stage further back when the nearest has no picture of the page', () => {
    const empty = new Map([['a', row('a')]]);

    expect(beforeSourceOf(subject, undefined, [empty, earlier])?.url).toBe(
      '/version-split/info.json',
    );
  });

  it('is the picture the page has now when no earlier stage made one and the stage has not either', () => {
    expect(beforeSourceOf(subject, row('a'), [new Map()])?.url).toBe('/page-a/info.json');
  });

  it('is nothing on the first stage, which has no stage before it', () => {
    expect(beforeSourceOf(subject, row('a'), [])).toBeNull();
  });

  it('is nothing when the stage has a result and nothing came before it, since the page then shows that result', () => {
    expect(beforeSourceOf(subject, row('a', { version: version('own') }), [new Map()])).toBeNull();
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
