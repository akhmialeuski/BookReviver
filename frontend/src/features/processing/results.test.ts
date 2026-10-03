import { describe, expect, it } from 'vitest';
import {
  DESKEW_METHODS_PARAMETERS,
  DESKEW_PARAMETERS,
  version,
} from '@/features/processing/fixtures';
import {
  describeParams,
  historyOf,
  parameterLabel,
  readChainResult,
  readResult,
  sourceSize,
} from '@/features/processing/results';

describe('readResult', () => {
  it('reads the angle, the confidence and whether the step left the page alone', () => {
    const result = readResult({ data: { angle: -2.4, confidence: 0.18, skipped: true } });

    expect(result).toMatchObject({ angle: -2.4, confidence: 0.18, skipped: true });
  });

  it('reads the place of a cut and the overlap of the halves', () => {
    expect(readResult({ data: { cut_x: 1200.5, overlap_px: 4 } })).toMatchObject({
      cutX: 1200.5,
      overlapPx: 4,
    });
  });

  it('reads the number of pages and works out the slant of a cut from its ends', () => {
    const result = readResult({
      data: { pages: 2, cut_top_x: 100, cut_bottom_x: 100 + Math.tan(0.05) * 999, height_px: 1000 },
    });

    expect(result.pages).toBe(2);
    expect(result.slantDeg).toBeCloseTo((0.05 * 180) / Math.PI, 6);
  });

  it('reads where the cut crosses the top and the bottom row of the scan', () => {
    expect(readResult({ data: { cut_top_x: 480, cut_bottom_x: 520 } })).toMatchObject({
      cutTopX: 480,
      cutBottomX: 520,
    });
  });

  it('gives no slant for a cut whose ends or whose height are not reported', () => {
    expect(readResult({ data: { cut_top_x: 100, height_px: 1000 } }).slantDeg).toBeNull();
    expect(readResult({ data: { cut_top_x: 100, cut_bottom_x: 90 } }).slantDeg).toBeNull();
  });

  it('leaves out what the step did not report or reported as something else', () => {
    const result = readResult({ data: { angle: 'steep', confidence: null } });

    expect(result).toEqual({
      angle: null,
      confidence: null,
      skipped: false,
      cutX: null,
      cutTopX: null,
      cutBottomX: null,
      overlapPx: null,
      pages: null,
      slantDeg: null,
      quad: null,
      frame: null,
      mesh: null,
      bend: null,
      lines: null,
      sourceWidthPx: null,
      sourceHeightPx: null,
    });
  });

  it('reads the curves a dewarping followed, how bent the lines were and how many there were', () => {
    const rows = [
      [
        { x: 0, y: 10 },
        { x: 50, y: 14 },
      ],
      [
        { x: 0, y: 90 },
        { x: 50, y: 97 },
      ],
    ];
    const result = readResult({ data: { mesh: { rows }, bend: 12.5, lines: 24 } });

    expect(result.mesh).toEqual({ rows });
    expect(result.bend).toBe(12.5);
    expect(result.lines).toBe(24);
  });

  it('reads the sheet and the frame a step found, and the size of the image it read', () => {
    const result = readResult({
      data: {
        quad: {
          top_left: { x: 1, y: 2 },
          top_right: { x: 101, y: 3 },
          bottom_right: { x: 99, y: 203 },
          bottom_left: { x: 0, y: 200 },
        },
        frame: { left: 10, top: 20, width: 80, height: 160 },
        source_width_px: 120,
        source_height_px: 240,
      },
    });

    expect(result.quad?.topRight).toEqual({ x: 101, y: 3 });
    expect(result.frame).toEqual({ left: 10, top: 20, width: 80, height: 160 });
    expect(sourceSize(result)).toEqual({ width: 120, height: 240 });
  });

  it('gives no size for a step that did not report one', () => {
    expect(sourceSize(readResult({ data: {} }))).toBeNull();
    expect(sourceSize(null)).toBeNull();
  });
});

describe('readChainResult', () => {
  it('takes what a step does not report from the nearest step before it that does', () => {
    const result = readChainResult([
      { data: { angle: 0.4, confidence: 0.9, skipped: false } },
      { data: { confidence: 0.7, skipped: true, frame: { left: 1, top: 2, width: 3, height: 4 } } },
    ]);

    expect(result?.angle).toBe(0.4);
    expect(result?.confidence).toBe(0.7);
    expect(result?.frame).toEqual({ left: 1, top: 2, width: 3, height: 4 });
  });

  it('leaves the page as it was only when every step left it', () => {
    expect(
      readChainResult([{ data: { skipped: true } }, { data: { skipped: true } }])?.skipped,
    ).toBe(true);
    expect(
      readChainResult([{ data: { skipped: false } }, { data: { skipped: true } }])?.skipped,
    ).toBe(false);
  });

  it('gives nothing for no versions', () => {
    expect(readChainResult([])).toBeNull();
  });
});

describe('historyOf', () => {
  const older = version('old', { created_at: '2026-10-01T10:00:00Z' });
  const newer = version('new', { created_at: '2026-10-01T12:00:00Z' });

  it('lists the newest result first and marks the current one', () => {
    const entries = historyOf([older, newer], 'old');

    expect(entries.map((entry) => entry.version.id)).toEqual(['new', 'old']);
    expect(entries.map((entry) => entry.current)).toEqual([false, true]);
  });

  it('leaves out a preview, a failed run and a run still going, which cannot be made current', () => {
    const entries = historyOf(
      [
        newer,
        version('p', { scale: 'preview' }),
        version('f', { state: 'failed' }),
        version('r', { state: 'running' }),
      ],
      undefined,
    );

    expect(entries.map((entry) => entry.version.id)).toEqual(['new']);
  });

  it('lists only the last step of a recipe, since the versions of the steps before it are not results', () => {
    const first = version('first', { created_at: '2026-10-01T10:00:00Z' });
    const last = version('last', { created_at: '2026-10-01T10:01:00Z', input_id: 'first' });
    const redone = version('redone', { created_at: '2026-10-01T12:00:00Z', input_id: 'first' });

    const entries = historyOf([first, last, redone], 'redone');

    expect(entries.map((entry) => entry.version.id)).toEqual(['redone', 'last']);
    expect(entries.map((entry) => entry.current)).toEqual([true, false]);
  });

  it('is empty for a page with no results', () => {
    expect(historyOf([], undefined)).toEqual([]);
  });
});

describe('parameterLabel', () => {
  it('is the title of the field in the schema', () => {
    expect(parameterLabel('max_angle', DESKEW_PARAMETERS)).toBe('Largest slant');
  });

  it('writes a name the schema does not know as words, never as the identifier', () => {
    expect(parameterLabel('search_band', DESKEW_PARAMETERS)).toBe('Search band');
    expect(parameterLabel('x', {})).toBe('X');
  });
});

describe('describeParams', () => {
  it('writes each parameter under its title, in the order of the schema', () => {
    const parts = describeParams({ min_confidence: 0.3, max_angle: 5 }, DESKEW_PARAMETERS);

    expect(parts).toEqual([
      { label: 'Largest slant', value: '5' },
      { label: 'Least confidence', value: '0.3' },
    ]);
  });

  it('puts a parameter the schema does not list after the ones it does', () => {
    const parts = describeParams({ extra: 1, max_angle: 5 }, DESKEW_PARAMETERS);

    expect(parts.map((part) => part.label)).toEqual(['Largest slant', 'Extra']);
  });

  it('describes the parameters of a processor with methods by the method they name', () => {
    const parts = describeParams(
      { min_line_share: 0.3, method: 'hough', max_angle: 5 },
      DESKEW_METHODS_PARAMETERS,
    );

    expect(parts).toEqual([
      { label: 'Largest slant', value: '5' },
      { label: 'Method', value: 'Long straight lines' },
      { label: 'Shortest line', value: '0.3' },
    ]);
  });

  it('describes parameters that name no method as the first one', () => {
    const parts = describeParams({ max_angle: 5 }, DESKEW_METHODS_PARAMETERS);

    expect(parts).toEqual([{ label: 'Largest slant', value: '5' }]);
  });
});
