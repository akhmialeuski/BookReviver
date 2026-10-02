import { describe, expect, it } from 'vitest';
import {
  HANDLE_ORDER,
  handlePoint,
  MIN_SIDE_PX,
  moveHandle,
  nudgeRect,
  RectHandle,
  rectOf,
} from '@/features/editors/rect';
import { readResult } from '@/features/processing/results';

/** The arithmetic of the frame editor, in the pixels of the image the step reads. */

const SIZE = { width: 1000, height: 1400 };
const FRAME = { left: 100, top: 200, width: 600, height: 800 };

describe('handlePoint', () => {
  it('stands the eight handles on the corners and the middles of the sides', () => {
    expect(HANDLE_ORDER.map((handle) => handlePoint(FRAME, handle))).toEqual([
      { x: 100, y: 200 },
      { x: 400, y: 200 },
      { x: 700, y: 200 },
      { x: 700, y: 600 },
      { x: 700, y: 1000 },
      { x: 400, y: 1000 },
      { x: 100, y: 1000 },
      { x: 100, y: 600 },
    ]);
  });
});

describe('moveHandle', () => {
  it('moves a corner and the two sides it holds', () => {
    expect(moveHandle(FRAME, RectHandle.TopLeft, { x: 50, y: 150 }, SIZE)).toEqual({
      left: 50,
      top: 150,
      width: 650,
      height: 850,
    });
  });

  it('moves a side with the middle handle and only that side', () => {
    expect(moveHandle(FRAME, RectHandle.Right, { x: 800, y: 5 }, SIZE)).toEqual({
      ...FRAME,
      width: 700,
    });
    expect(moveHandle(FRAME, RectHandle.Top, { x: 5, y: 100 }, SIZE)).toEqual({
      ...FRAME,
      top: 100,
      height: 900,
    });
  });

  it('keeps the frame on the image', () => {
    expect(moveHandle(FRAME, RectHandle.BottomRight, { x: 1500, y: 2000 }, SIZE)).toEqual({
      ...FRAME,
      width: 900,
      height: 1200,
    });
  });

  it('never makes the frame thinner than the least side', () => {
    const squeezed = moveHandle(FRAME, RectHandle.Left, { x: 900, y: 0 }, SIZE);

    expect(squeezed.width).toBe(MIN_SIDE_PX);
    expect(squeezed.left + squeezed.width).toBe(700);
  });
});

describe('nudgeRect', () => {
  it('moves the whole frame and keeps it on the image', () => {
    expect(nudgeRect(FRAME, 10, -10, SIZE)).toEqual({ ...FRAME, left: 110, top: 190 });
    expect(nudgeRect(FRAME, 5000, 5000, SIZE)).toEqual({ ...FRAME, left: 400, top: 600 });
  });
});

describe('rectOf', () => {
  it('starts from the frame the step found, else from the image less a free margin', () => {
    const found = readResult({ data: { frame: FRAME } });

    expect(rectOf(found, SIZE)).toEqual(FRAME);
    expect(rectOf(null, SIZE)).toEqual({ left: 100, top: 140, width: 800, height: 1120 });
  });
});
