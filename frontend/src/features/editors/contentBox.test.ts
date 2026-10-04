import { describe, expect, it } from 'vitest';
import {
  contentBoxOf,
  marginAt,
  marginsOf,
  outerOf,
  sameMargins,
  settingOf,
  sidePoint,
  withMargin,
} from '@/features/editors/contentBox';
import { readResult } from '@/features/processing/results';

/** The arithmetic of the Margins editor. */

const FOUND = readResult({
  data: {
    content_box: { left: 100, top: 200, width: 300, height: 400 },
    margin_box: { left: 70, top: 150, width: 380, height: 520 },
    block_scale: 0.5,
  },
});
const BOX = { left: 100, top: 200, width: 300, height: 400 };

describe('marginsOf', () => {
  it('reads the margins as the distance of the border from the box on each side', () => {
    expect(marginsOf(FOUND)).toEqual({ left: 30, top: 50, right: 50, bottom: 70 });
  });

  it('gives none when the step recorded no border, or no result is there', () => {
    expect(marginsOf(readResult({ data: {} }))).toBeNull();
    expect(marginsOf(null)).toBeNull();
  });
});

describe('outerOf', () => {
  it('grows the box by the margins', () => {
    expect(outerOf(BOX, { left: 30, top: 50, right: 50, bottom: 70 })).toEqual({
      left: 70,
      top: 150,
      width: 380,
      height: 520,
    });
  });

  it('follows the box when it moves and keeps the margins', () => {
    const margins = marginsOf(FOUND);
    expect(margins).not.toBeNull();
    const moved = outerOf(
      { ...BOX, left: 110, top: 190 },
      margins ?? { left: 0, top: 0, right: 0, bottom: 0 },
    );

    expect(moved.left).toBe(80);
    expect(moved.top).toBe(140);
  });
});

describe('sidePoint', () => {
  it('gives the middle of each side', () => {
    expect(sidePoint(BOX, 'left')).toEqual({ x: 100, y: 400 });
    expect(sidePoint(BOX, 'right')).toEqual({ x: 400, y: 400 });
    expect(sidePoint(BOX, 'top')).toEqual({ x: 250, y: 200 });
    expect(sidePoint(BOX, 'bottom')).toEqual({ x: 250, y: 600 });
  });
});

describe('marginAt', () => {
  it('measures a side dragged to a point from the same side of the box', () => {
    expect(marginAt(BOX, 'left', { x: 60, y: 400 })).toBe(40);
    expect(marginAt(BOX, 'right', { x: 460, y: 400 })).toBe(60);
    expect(marginAt(BOX, 'top', { x: 250, y: 120 })).toBe(80);
    expect(marginAt(BOX, 'bottom', { x: 250, y: 650 })).toBe(50);
  });

  it('keeps the border outside the box', () => {
    expect(marginAt(BOX, 'left', { x: 150, y: 400 })).toBe(0);
    expect(marginAt(BOX, 'bottom', { x: 250, y: 500 })).toBe(0);
  });
});

describe('settingOf', () => {
  it('turns a distance on the picture into whole pixels of the page, by the scale of the box', () => {
    expect(settingOf(50, 0.5)).toBe(25);
    expect(settingOf(33, 0.8)).toBe(26);
  });
});

describe('withMargin and sameMargins', () => {
  const margins = { left: 30, top: 50, right: 50, bottom: 70 };

  it('changes one side and no other', () => {
    expect(withMargin(margins, 'top', 12)).toEqual({ ...margins, top: 12 });
  });

  it('compares margins to the pixel', () => {
    expect(sameMargins(margins, { ...margins, top: 50.3 })).toBe(true);
    expect(sameMargins(margins, { ...margins, top: 52 })).toBe(false);
  });
});

describe('contentBoxOf', () => {
  it('starts from the box the step found', () => {
    expect(contentBoxOf(FOUND, { width: 1000, height: 1500 })).toEqual(BOX);
  });

  it('starts from the picture less a free margin when the step found none', () => {
    expect(contentBoxOf(null, { width: 1000, height: 1500 })).toEqual({
      left: 100,
      top: 150,
      width: 800,
      height: 1200,
    });
  });
});
