import { describe, expect, it } from 'vitest';
import { editableStepsOf, editorOf, hasEditor } from '@/features/editors/registry';
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
import { readResult, sourceSize } from '@/features/processing/results';
import { page, row } from '@/features/workspace/fixtures';

/** The registry of page editors: which kinds have a component, and what each one is built to do. */

describe('hasEditor', () => {
  it.each(['line', 'rotation', 'split', 'quad', 'rect', 'mesh', 'regions', 'brush-mask'] as const)(
    'has a component for %s',
    (kind) => {
      expect(hasEditor(kind)).toBe(true);
    },
  );

  it.each(['none'] as const)('has none yet for %s', (kind) => {
    expect(hasEditor(kind)).toBe(false);
  });
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
    const context = {
      current,
      items: [current],
      scan: scan('s', 100, 50),
      stepInput: null,
      result: null,
      processorKey: 'geometry.deskew',
    };

    expect(editorOf('rotation').fallback({ ...context, size: null })).toEqual({
      degrees: 0,
    });
    expect(editorOf('line').fallback({ ...context, size: { width: 100, height: 50 } })).toEqual({
      start: { x: 50, y: 0 },
      end: { x: 50, y: 50 },
    });
  });

  it('starts the choice of pages from two pages cut along the line the step found', () => {
    const current = { page: page('p'), row: row('p') };
    const context = {
      current,
      items: [current],
      scan: scan('s', 100, 50),
      stepInput: null,
      result: null,
      processorKey: 'split.auto',
    };

    expect(editorOf('split').fallback({ ...context, size: { width: 100, height: 50 } })).toEqual({
      pages: 2,
      line: { start: { x: 50, y: 0 }, end: { x: 50, y: 50 } },
    });
  });

  it('runs the stage after a save of the choice also on a scan kept whole, and after a line only on a cut one', () => {
    const whole = { page: page('p', { scan_id: 's', slot: 0 }), row: row('p') };
    const context = {
      current: whole,
      items: [whole],
      scan: scan('s', 100, 50),
      stepInput: null,
      result: null,
      processorKey: 'split.auto',
    };

    expect(editorOf('split').runsAfterEdit(context)).toBe(true);
    expect(editorOf('line').runsAfterEdit(context)).toBe(false);
  });

  it('lays the sheet and the frame on what their step reads, and offers them once their step has run', () => {
    expect(editorOf('quad').picture).toBe(Picture.Input);
    expect(editorOf('rect').picture).toBe(Picture.Input);
    expect(editorOf('quad').needsResult).toBe(true);
    expect(editorOf('rect').needsResult).toBe(true);
    expect(editorOf('rotation').needsResult).toBe(false);
  });

  it('starts the sheet from the corners the step found and the frame from the frame it found', () => {
    const current = { page: page('p'), row: row('p') };
    const result = readResult({
      data: {
        quad: {
          top_left: { x: 5, y: 6 },
          top_right: { x: 95, y: 4 },
          bottom_right: { x: 97, y: 196 },
          bottom_left: { x: 3, y: 198 },
        },
        frame: { left: 10, top: 20, width: 70, height: 150 },
        source_width_px: 100,
        source_height_px: 200,
      },
    });
    const context = {
      current,
      items: [current],
      scan: null,
      stepInput: null,
      result,
      processorKey: 'geometry.crop',
      size: sourceSize(result),
    };

    expect(editorOf('quad').fallback(context)).toEqual({
      top_left: { x: 5, y: 6 },
      top_right: { x: 95, y: 4 },
      bottom_right: { x: 97, y: 196 },
      bottom_left: { x: 3, y: 198 },
    });
    expect(editorOf('rect').fallback(context)).toEqual({
      left: 10,
      top: 20,
      width: 70,
      height: 150,
    });
    expect(editorOf('quad').size(context)).toEqual({ width: 100, height: 200 });
  });

  it('lays the curves on what the dewarping reads and starts them from the curves the step found', () => {
    const current = { page: page('p'), row: row('p') };
    const rows = [
      [
        { x: 0, y: 20 },
        { x: 100, y: 30 },
      ],
      [
        { x: 0, y: 160 },
        { x: 100, y: 190 },
      ],
    ];
    const result = readResult({
      data: { mesh: { rows }, source_width_px: 100, source_height_px: 200 },
    });
    const context = {
      current,
      items: [current],
      scan: null,
      stepInput: null,
      result,
      processorKey: 'geometry.dewarp',
      size: sourceSize(result),
    };

    expect(editorOf('mesh').picture).toBe(Picture.Input);
    expect(editorOf('mesh').needsResult).toBe(true);
    expect(editorOf('mesh').alwaysOn).toBe(false);
    expect(editorOf('mesh').runsAfterEdit(context)).toBe(true);
    expect(editorOf('mesh').fallback(context)).toEqual({ rows });
    expect(editorOf('mesh').fallback({ ...context, result: null })).toEqual({
      rows: [expect.arrayContaining([{ x: 0, y: 20 }]), expect.arrayContaining([{ x: 0, y: 180 }])],
    });
  });
});

describe('the editors of the cleanup', () => {
  const current = { page: page('p'), row: row('p') };
  const result = readResult({ data: { source_width_px: 1000, source_height_px: 1500 } });
  const context = {
    current,
    items: [current],
    scan: null,
    stepInput: null,
    result,
    processorKey: 'cleanup.binarize',
    size: sourceSize(result),
  };

  it('lays both on what their step reads, offers them once their step has run, and keeps both behind Set by hand', () => {
    for (const kind of ['regions', 'brush-mask'] as const) {
      expect(editorOf(kind).picture).toBe(Picture.Input);
      expect(editorOf(kind).needsResult).toBe(true);
      expect(editorOf(kind).alwaysOn).toBe(false);
      expect(editorOf(kind).runsAfterEdit(context)).toBe(true);
      expect(editorOf(kind).size(context)).toEqual({ width: 1000, height: 1500 });
    }
  });

  it('starts the zones and the strokes from none, so a page the reader has not touched is found again by the step', () => {
    expect(editorOf('regions').fallback(context)).toEqual({ zones: [] });
    expect(editorOf('brush-mask').fallback(context)).toEqual({ strokes: [] });
  });

  it('paints a mask for the brush and for no other editor', () => {
    expect(editorOf('brush-mask').mask).not.toBeNull();
    for (const kind of ['line', 'rotation', 'split', 'quad', 'rect', 'regions'] as const) {
      expect(editorOf(kind).mask).toBeNull();
    }
  });

  it('refuses to paint a mask from a geometry that holds no strokes', async () => {
    await expect(
      editorOf('brush-mask').mask?.({ left: 1 }, { width: 10, height: 10 }),
    ).rejects.toThrow('does not fit');
  });

  it('offers the editor of the binarization and of the eraser from the recipe of the stage', () => {
    const cleanup = [
      processor('cleanup.binarize', { stage: 'cleanup', editor: 'regions' }),
      processor('cleanup.despeckle', { stage: 'cleanup' }),
      processor('cleanup.eraser', { stage: 'cleanup', editor: 'brush-mask' }),
    ];
    const text = recipe('t', {
      stage: 'cleanup',
      steps: [step('cleanup.binarize'), step('cleanup.despeckle'), step('cleanup.eraser')],
    });

    expect(editableStepsOf(text, cleanup).map((entry) => entry.kind)).toEqual([
      'regions',
      'brush-mask',
    ]);
  });
});

describe('editableStepsOf', () => {
  const catalogue = [
    processor('geometry.perspective', { editor: 'quad' }),
    deskew(),
    processor('geometry.crop', { editor: 'rect' }),
    spread(),
  ];

  it('gives every enabled step that has an editor, in the order of the recipe', () => {
    const geometry = recipe('g', {
      steps: [step('geometry.perspective'), step('geometry.deskew'), step('geometry.crop')],
    });

    expect(editableStepsOf(geometry, catalogue).map((entry) => entry.kind)).toEqual([
      'quad',
      'rotation',
      'rect',
    ]);
  });

  it('leaves out a step that is off, and gives none for no recipe', () => {
    const geometry = recipe('g', {
      steps: [step('geometry.perspective', { enabled: false }), step('geometry.crop')],
    });

    expect(
      editableStepsOf(geometry, catalogue).map((entry) => [entry.index, entry.processor.key]),
    ).toEqual([[1, 'geometry.crop']]);
    expect(editableStepsOf(undefined, catalogue)).toEqual([]);
  });

  it('gives both steps of a processor the recipe runs twice, each with its own identifier', () => {
    const twice = recipe('t', {
      steps: [
        step('geometry.deskew', { step_id: 'first' }),
        step('geometry.deskew', { step_id: 'second' }),
      ],
    });

    expect(editableStepsOf(twice, catalogue).map((entry) => entry.step.step_id)).toEqual([
      'first',
      'second',
    ]);
  });

  it('does not take the first processor of the catalogue for the one the recipe uses', () => {
    const splits = [spread(), autoSplit()];
    const auto = recipe('a', { steps: [step('split.auto')] });
    const cut = recipe('c', { steps: [step('split.spread')] });

    expect(editableStepsOf(auto, splits).map((entry) => entry.kind)).toEqual(['split']);
    expect(editableStepsOf(cut, splits).map((entry) => entry.kind)).toEqual(['line']);
  });

  it.each([
    ['a recipe whose steps offer no editor', recipe('n', { steps: [step('geometry.crop')] })],
    [
      'a recipe whose step is off',
      recipe('o', { steps: [step('split.auto', { enabled: false })] }),
    ],
    ['a step the catalogue does not know', recipe('u', { steps: [step('split.unknown')] })],
  ])('gives none for %s', (_name, shown) => {
    const known = [spread(), autoSplit(), processor('geometry.crop'), deskew()];

    expect(editableStepsOf(shown, known)).toEqual([]);
  });
});
