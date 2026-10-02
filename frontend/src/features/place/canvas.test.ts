import { describe, expect, it } from 'vitest';
import { fittedWidth, positionOf, viewportZoom } from '@/features/place/canvas';

const PORTRAIT_PAGE = { width: 0.7, height: 1 };
const WIDE_SCREEN_ASPECT = 2;
const PHONE_ASPECT = 0.5;

describe('fittedWidth', () => {
  it('is the width of the viewport in world units when the height of the view fills it', () => {
    expect(fittedWidth(PORTRAIT_PAGE, WIDE_SCREEN_ASPECT)).toBe(2);
  });

  it('is the width of the view when the width of the view fills the viewport', () => {
    expect(fittedWidth(PORTRAIT_PAGE, PHONE_ASPECT)).toBe(0.7);
  });

  it('counts a viewport of no usable shape as a square', () => {
    for (const aspect of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(fittedWidth(PORTRAIT_PAGE, aspect)).toBe(1);
    }
  });
});

describe('positionOf', () => {
  it('is 1 at the zoom that fits the view', () => {
    const fitted = fittedWidth(PORTRAIT_PAGE, WIDE_SCREEN_ASPECT);
    expect(positionOf(1 / fitted, { x: 0.35, y: 0.5 }, fitted)).toEqual({
      zoom: 1,
      centre_x: 0.35,
      centre_y: 0.5,
    });
  });

  it('grows with the zoom of the viewport', () => {
    const fitted = fittedWidth(PORTRAIT_PAGE, WIDE_SCREEN_ASPECT);
    expect(positionOf(4 / fitted, { x: 0, y: 0 }, fitted)?.zoom).toBe(4);
  });

  it('keeps four decimals, so a restored view reads back as the stored one', () => {
    expect(positionOf(1, { x: 0.123456, y: 0.987654 }, 1)).toEqual({
      zoom: 1,
      centre_x: 0.1235,
      centre_y: 0.9877,
    });
  });

  it('is nothing for a number that is not finite or a zoom of zero', () => {
    expect(positionOf(Number.NaN, { x: 0, y: 0 }, 1)).toBeNull();
    expect(positionOf(1, { x: Number.POSITIVE_INFINITY, y: 0 }, 1)).toBeNull();
    expect(positionOf(0, { x: 0, y: 0 }, 1)).toBeNull();
  });
});

describe('viewportZoom', () => {
  it('shows the same share of the view on a window of another shape', () => {
    const position = { zoom: 3, centre_x: 0.4, centre_y: 0.5 };
    const wide = fittedWidth(PORTRAIT_PAGE, WIDE_SCREEN_ASPECT);
    const narrow = fittedWidth(PORTRAIT_PAGE, PHONE_ASPECT);
    expect(positionOf(viewportZoom(position, wide), { x: 0.4, y: 0.5 }, wide)).toEqual(position);
    expect(positionOf(viewportZoom(position, narrow), { x: 0.4, y: 0.5 }, narrow)).toEqual(
      position,
    );
    expect(viewportZoom(position, wide)).not.toBe(viewportZoom(position, narrow));
  });
});
