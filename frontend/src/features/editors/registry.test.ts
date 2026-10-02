import { describe, expect, it } from 'vitest';
import { editedProcessorOf, editorOf, hasEditor } from '@/features/editors/registry';
import { Picture } from '@/features/editors/types';
import {
  autoSplit,
  deskew,
  processor,
  recipe,
  scan,
  spread,
  step,
} from '@/features/processing/fixtures';
import { page, row } from '@/features/workspace/fixtures';

/** The registry of page editors: which kinds have a component, and what each one is built to do. */

describe('hasEditor', () => {
  it.each(['line', 'rotation', 'split'] as const)('has a component for %s', (kind) => {
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

  it('lays the choice of pages on the scan and keeps it open, like the split line it draws', () => {
    expect(editorOf('split').picture).toBe(Picture.Scan);
    expect(editorOf('split').alwaysOn).toBe(true);
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

  it('starts the choice of pages from two pages cut along the line the step found', () => {
    const current = { page: page('p'), row: row('p') };
    const context = { current, items: [current], scan: scan('s', 100, 50) };

    expect(
      editorOf('split').fallback({ ...context, size: { width: 100, height: 50 }, result: null }),
    ).toEqual({ pages: 2, line: { start: { x: 50, y: 0 }, end: { x: 50, y: 50 } } });
  });

  it('runs the stage after a save of the choice also on a scan kept whole, and after a line only on a cut one', () => {
    const whole = { page: page('p', { scan_id: 's', slot: 0 }), row: row('p') };
    const context = { current: whole, items: [whole], scan: scan('s', 100, 50) };

    expect(editorOf('split').runsAfterEdit(context)).toBe(true);
    expect(editorOf('line').runsAfterEdit(context)).toBe(false);
  });
});

describe('editedProcessorOf', () => {
  const catalogue = [spread(), autoSplit(), processor('geometry.crop'), deskew()];

  it('gives the processor of the first step that has an editor, in the order of the recipe', () => {
    const auto = recipe('a', { steps: [step('geometry.crop'), step('split.auto')] });

    expect(editedProcessorOf(auto, catalogue)?.key).toBe('split.auto');
  });

  it('does not take the first processor of the catalogue for the one the recipe uses', () => {
    const auto = recipe('a', { steps: [step('split.auto')] });
    const cut = recipe('c', { steps: [step('split.spread')] });

    expect(editedProcessorOf(auto, catalogue)?.editor).toBe('split');
    expect(editedProcessorOf(cut, catalogue)?.editor).toBe('line');
  });

  it.each([
    ['no recipe', undefined],
    ['a recipe whose steps offer no editor', recipe('n', { steps: [step('geometry.crop')] })],
    [
      'a recipe whose step is off',
      recipe('o', { steps: [step('split.auto', { enabled: false })] }),
    ],
    ['a step the catalogue does not know', recipe('u', { steps: [step('split.unknown')] })],
  ])('gives none for %s', (_name, shown) => {
    expect(editedProcessorOf(shown, catalogue)).toBeUndefined();
  });
});
