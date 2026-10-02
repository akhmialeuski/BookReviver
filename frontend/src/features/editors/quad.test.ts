import { describe, expect, it } from 'vitest';
import {
  isConvex,
  moveCorner,
  pointsOf,
  QuadCorner,
  quadOf,
  wholeImage,
} from '@/features/editors/quad';
import { readResult } from '@/features/processing/results';

/** The arithmetic of the sheet editor, in the pixels of the image the step reads. */

const SIZE = { width: 1000, height: 1400 };

describe('wholeImage', () => {
  it('stands the corners on the corners of the image, in the order round the sheet', () => {
    expect(pointsOf(wholeImage(SIZE))).toEqual([
      { x: 0, y: 0 },
      { x: 1000, y: 0 },
      { x: 1000, y: 1400 },
      { x: 0, y: 1400 },
    ]);
  });
});

describe('isConvex', () => {
  it('accepts a sheet seen from a slant and refuses one that is folded or flat', () => {
    expect(isConvex({ ...wholeImage(SIZE), topLeft: { x: 40, y: 0 } })).toBe(true);
    expect(isConvex({ ...wholeImage(SIZE), topLeft: { x: 1100, y: 1500 } })).toBe(false);
    expect(
      isConvex({
        topLeft: { x: 0, y: 0 },
        topRight: { x: 10, y: 0 },
        bottomRight: { x: 20, y: 0 },
        bottomLeft: { x: 30, y: 0 },
      }),
    ).toBe(false);
  });
});

describe('moveCorner', () => {
  it('moves a corner to the pointer', () => {
    const moved = moveCorner(wholeImage(SIZE), QuadCorner.TopLeft, { x: 30, y: 20 }, SIZE);

    expect(moved.topLeft).toEqual({ x: 30, y: 20 });
    expect(moved.bottomRight).toEqual({ x: 1000, y: 1400 });
  });

  it('keeps a corner on the image', () => {
    const moved = moveCorner(wholeImage(SIZE), QuadCorner.BottomRight, { x: 1200, y: 1600 }, SIZE);

    expect(moved.bottomRight).toEqual({ x: 1000, y: 1400 });
  });

  it('leaves the sheet as it was when the move would fold it', () => {
    const sheet = wholeImage(SIZE);

    expect(moveCorner(sheet, QuadCorner.TopLeft, { x: 900, y: 1300 }, SIZE)).toBe(sheet);
  });
});

describe('quadOf', () => {
  it('starts from the corners the step found, else from the whole image', () => {
    const found = readResult({
      data: {
        quad: {
          top_left: { x: 5, y: 6 },
          top_right: { x: 95, y: 4 },
          bottom_right: { x: 97, y: 196 },
          bottom_left: { x: 3, y: 198 },
        },
      },
    });

    expect(quadOf(found, SIZE).topRight).toEqual({ x: 95, y: 4 });
    expect(quadOf(readResult({ data: {} }), SIZE)).toEqual(wholeImage(SIZE));
    expect(quadOf(null, SIZE)).toEqual(wholeImage(SIZE));
  });
});
