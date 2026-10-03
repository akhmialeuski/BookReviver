import { describe, expect, it } from 'vitest';
import { pictureOf } from '@/features/editors/picture';
import { Picture } from '@/features/editors/types';
import { SourceKind } from '@/features/processing/compare';
import { scan, version } from '@/features/processing/fixtures';
import { images, page } from '@/features/workspace/fixtures';

/** The picture an editor lies on: the scan for the split line, the picture the step reads for the rotation. */

const BEFORE = { kind: SourceKind.Iiif, url: '/before/info.json' } as const;

describe('pictureOf', () => {
  it('gives the pyramid of the scan for an editor that works in the pixels of the scan', () => {
    expect(pictureOf(Picture.Scan, scan('s', 100, 50), page('p'), BEFORE)).toEqual({
      kind: SourceKind.Iiif,
      url: '/scan-s/info.json',
    });
  });

  it('gives nothing for a scan whose pyramid is not cut yet, or for a page without a scan', () => {
    expect(
      pictureOf(Picture.Scan, { ...scan('s', 100, 50), images: null }, page('p'), BEFORE),
    ).toBeNull();
    expect(pictureOf(Picture.Scan, null, page('p'), BEFORE)).toBeNull();
  });

  it('gives the picture before the stage for an editor that works on what the step reads', () => {
    expect(pictureOf(Picture.Input, null, page('p'), BEFORE)).toBe(BEFORE);
  });

  it('falls back to the image the page has when no earlier stage made one', () => {
    expect(pictureOf(Picture.Input, null, page('p'), null)).toEqual({
      kind: SourceKind.Iiif,
      url: '/page-p/info.json',
    });
  });

  it('gives what the step before made for a step that is not the first of the stage', () => {
    const made = version('v1', { tiles_ready: false, images: images('made') });

    expect(pictureOf(Picture.Input, null, page('p'), BEFORE, made)).toEqual({
      kind: SourceKind.Image,
      url: '/made/preview',
    });
    expect(
      pictureOf(Picture.Input, null, page('p'), BEFORE, { ...made, tiles_ready: true }),
    ).toEqual({ kind: SourceKind.Iiif, url: '/made/info.json' });
  });

  it('gives the page the step made itself for an editor that lies on the page of the book', () => {
    const made = version('v2', { tiles_ready: true, images: images('page') });

    expect(pictureOf(Picture.Output, null, page('p'), BEFORE, null, made)).toEqual({
      kind: SourceKind.Iiif,
      url: '/page/info.json',
    });
    expect(pictureOf(Picture.Output, null, page('p'), BEFORE, null, null)).toBeNull();
  });

  it('gives nothing for a page without an image', () => {
    expect(pictureOf(Picture.Input, null, page('p', { images: null }), null)).toBeNull();
  });
});
