import { describe, expect, it } from 'vitest';
import { editorOf, hasEditor } from '@/features/editors/registry';
import { Picture } from '@/features/editors/types';
import { scan } from '@/features/processing/fixtures';
import { page, row } from '@/features/workspace/fixtures';

/** The registry of page editors: which kinds have a component, and what each one is built to do. */

describe('hasEditor', () => {
  it.each(['line', 'rotation'] as const)('has a component for %s', (kind) => {
    expect(hasEditor(kind)).toBe(true);
  });

  it.each(['none', 'rect', 'quad', 'mesh', 'brush-mask', 'regions'] as const)(
    'has none yet for %s',
    (kind) => {
      expect(hasEditor(kind)).toBe(false);
    },
  );
});

describe('editorOf', () => {
  it('puts the split line on the scan and the rotation on what the step reads', () => {
    expect(editorOf('line').picture).toBe(Picture.Scan);
    expect(editorOf('rotation').picture).toBe(Picture.Input);
  });

  it('keeps the split line open on its stage and the rotation behind Set by hand', () => {
    expect(editorOf('line').alwaysOn).toBe(true);
    expect(editorOf('rotation').alwaysOn).toBe(false);
  });

  it('gives the fallback of an editor as the geometry the server stores', () => {
    const current = { page: page('p'), row: row('p') };
    const context = { current, items: [current], scan: scan('s', 100, 50) };

    expect(editorOf('rotation').fallback({ ...context, size: null, result: null })).toEqual({
      degrees: 0,
    });
    expect(
      editorOf('line').fallback({ ...context, size: { width: 100, height: 50 }, result: null }),
    ).toEqual({ start: { x: 50, y: 0 }, end: { x: 50, y: 50 } });
  });
});
