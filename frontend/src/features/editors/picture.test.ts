import { describe, expect, it } from 'vitest';
import { pictureOf } from '@/features/editors/picture';
import { Picture } from '@/features/editors/types';
import { SourceKind } from '@/features/processing/compare';
import { scan, version } from '@/features/processing/fixtures';
import { images } from '@/features/workspace/fixtures';

/** The picture an editor lies on: the scan for the split line, the picture the step reads for the rotation. */

const BEFORE = { kind: SourceKind.Iiif, url: '/before/info.json' } as const;

describe('pictureOf', () => {
  it('gives the pyramid of the scan for an editor that works in the pixels of the scan', () => {
    expect(pictureOf(Picture.Scan, scan('s', 100, 50), BEFORE)).toEqual({
      kind: SourceKind.Iiif,
      url: '/scan-s/info.json',
    });
  });

  it('gives nothing for a scan whose pyramid is not cut yet, or for a page without a scan', () => {
    expect(pictureOf(Picture.Scan, { ...scan('s', 100, 50), images: null }, BEFORE)).toBeNull();
    expect(pictureOf(Picture.Scan, null, BEFORE)).toBeNull();
  });

  it('gives the picture the server gives for the page, which is what the step reads, for an editor that works on it', () => {
    expect(pictureOf(Picture.Input, null, BEFORE)).toBe(BEFORE);
  });

  it('gives the picture of the server for an editor on the input, whatever version the step made itself', () => {
    const made = version('v1', { tiles_ready: true, images: images('made') });

    expect(pictureOf(Picture.Input, null, BEFORE, made)).toBe(BEFORE);
  });

  it('gives nothing while the picture of the page is not there, never the image the page has', () => {
    expect(pictureOf(Picture.Input, null, null)).toBeNull();
  });

  it('gives the page the step made itself for an editor that lies on the page of the book', () => {
    const made = version('v2', { tiles_ready: true, images: images('page') });

    expect(pictureOf(Picture.Output, null, BEFORE, made)).toEqual({
      kind: SourceKind.Iiif,
      url: '/page/info.json',
    });
    expect(pictureOf(Picture.Output, null, BEFORE, null)).toBeNull();
  });
});
