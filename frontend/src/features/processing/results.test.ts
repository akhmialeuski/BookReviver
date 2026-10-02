import { describe, expect, it } from 'vitest';
import { DESKEW_PARAMETERS, version } from '@/features/processing/fixtures';
import {
  describeParams,
  historyOf,
  parameterLabel,
  readResult,
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
    });
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
});
