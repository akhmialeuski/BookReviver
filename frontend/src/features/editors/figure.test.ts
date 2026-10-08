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

describe('the shape set by hand', () => {
  it('is painted orange, a red channel far above the green and the blue far below it, and solid', () => {
    const { stroke, dash } = FIGURE_STYLE['by-hand'];
    const [red = 0, green = 0, blue = 0] = [1, 3, 5].map((at) =>
      Number.parseInt(stroke.slice(at, at + 2), 16),
    );

    expect(stroke).toMatch(/^#[0-9a-f]{6}$/);
    expect(red).toBeGreaterThan(200);
    expect(green).toBeGreaterThan(blue + 40);
    expect(red).toBeGreaterThan(green + 80);
    expect(dash).toBeUndefined();
  });
});
