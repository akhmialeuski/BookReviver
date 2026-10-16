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
  it('is painted dark green, the green channel far above the red and the blue and darker than the found shape, and solid', () => {
    const { stroke, dash } = FIGURE_STYLE['by-hand'];
    const channels = (colour: string): number[] =>
      [1, 3, 5].map((at) => Number.parseInt(colour.slice(at, at + 2), 16));
    const [red = 0, green = 0, blue = 0] = channels(stroke);
    const [, foundGreen = 0] = channels(FIGURE_STYLE.found.stroke);

    expect(stroke).toBe('#047857');
    expect(green).toBeGreaterThan(red + 40);
    expect(green).toBeGreaterThan(blue);
    expect(green).toBeLessThan(foundGreen);
    expect(dash).toBeUndefined();
  });

  it('is drawn with a line of 3 px against the 2 px of the shape the step found', () => {
    expect(FIGURE_STYLE['by-hand'].lineWidth).toBe(3);
    expect(FIGURE_STYLE.found.lineWidth).toBe(2);
    expect(FIGURE_STYLE['by-hand'].lineWidth).toBeGreaterThan(FIGURE_STYLE.found.lineWidth);
  });
});
