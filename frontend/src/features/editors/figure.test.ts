import { describe, expect, it } from 'vitest';
import { FIGURE_STYLE, figureStateOf } from '@/features/editors/figure';

/** The state a shape is in on a page, and the look each state has. */

describe('figureStateOf', () => {
  it('is the default before the step has run and nothing was set', () => {
    expect(figureStateOf(false, false)).toBe('default');
  });

  it('is found once the step has made a version', () => {
    expect(figureStateOf(false, true)).toBe('found');
  });

  it('is set by hand whenever the reader set it, whether or not the step ran', () => {
    expect(figureStateOf(true, false)).toBe('by-hand');
    expect(figureStateOf(true, true)).toBe('by-hand');
  });
});

describe('FIGURE_STYLE', () => {
  it('draws the default dashed and grey, and found and by-hand as solid lines of their own colours', () => {
    expect(FIGURE_STYLE.default.dash).toBeDefined();
    expect(FIGURE_STYLE.found.dash).toBeUndefined();
    expect(FIGURE_STYLE['by-hand'].dash).toBeUndefined();
    expect(
      new Set([
        FIGURE_STYLE.default.stroke,
        FIGURE_STYLE.found.stroke,
        FIGURE_STYLE['by-hand'].stroke,
      ]).size,
    ).toBe(3);
  });
});
