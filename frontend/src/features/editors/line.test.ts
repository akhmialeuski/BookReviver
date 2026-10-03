import { describe, expect, it } from 'vitest';
import {
  crossingX,
  cutLine,
  LineEnd,
  MIN_SPAN_PX,
  moveEnd,
  NUDGE_PX,
  NUDGE_SHIFT_PX,
  nudgeLine,
  nudgeOfKey,
  verticalLine,
} from '@/features/editors/line';

/** The arithmetic of the split line, in the pixels of the scan. */

const SCAN = { width: 1000, height: 600 };

describe('cutLine', () => {
  const found = {
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
  };

  it('follows the slanted cut a step reported by its two ends', () => {
    expect(cutLine({ ...found, cutX: 500, cutTopX: 460, cutBottomX: 540 }, SCAN)).toEqual({
      start: { x: 460, y: 0 },
      end: { x: 540, y: 600 },
    });
  });

  it('falls back to the vertical cut, and then to the middle of the scan', () => {
    expect(cutLine({ ...found, cutX: 420 }, SCAN)).toEqual(verticalLine(420, SCAN));
    expect(cutLine({ ...found, cutTopX: 460 }, SCAN)).toEqual(verticalLine(500, SCAN));
    expect(cutLine(null, SCAN)).toEqual(verticalLine(500, SCAN));
  });
});

describe('verticalLine', () => {
  it('runs from the top of the scan to its bottom', () => {
    expect(verticalLine(480, SCAN)).toEqual({
      start: { x: 480, y: 0 },
      end: { x: 480, y: 600 },
    });
  });

  it('keeps the line on the scan', () => {
    expect(verticalLine(-5, SCAN).start.x).toBe(0);
    expect(verticalLine(5000, SCAN).end.x).toBe(SCAN.width);
  });
});

describe('moveEnd', () => {
  const line = verticalLine(500, SCAN);

  it('moves the end that was taken and leaves the other', () => {
    const moved = moveEnd(line, LineEnd.Start, { x: 520, y: 30 }, SCAN);

    expect(moved.start).toEqual({ x: 520, y: 30 });
    expect(moved.end).toEqual(line.end);
  });

  it('keeps an end on the scan', () => {
    const moved = moveEnd(line, LineEnd.End, { x: 5000, y: 5000 }, SCAN);

    expect(moved.end).toEqual({ x: SCAN.width, y: SCAN.height });
  });

  it('refuses a move that would make the line horizontal, which the server cannot cut along', () => {
    const moved = moveEnd(line, LineEnd.Start, { x: 100, y: SCAN.height - MIN_SPAN_PX / 2 }, SCAN);

    expect(moved).toBe(line);
  });
});

describe('nudgeLine', () => {
  const line = verticalLine(500, SCAN);

  it('moves both ends together', () => {
    expect(nudgeLine(line, 10, 0, SCAN)).toEqual(verticalLine(510, SCAN));
  });

  it('stops at the edge of the scan with the line unbent', () => {
    const moved = nudgeLine({ start: { x: 3, y: 0 }, end: { x: 40, y: 600 } }, -10, 0, SCAN);

    expect(moved).toEqual({ start: { x: 0, y: 0 }, end: { x: 37, y: 600 } });
  });

  it('does not move a line that already spans the height up or down', () => {
    expect(nudgeLine(line, 0, 25, SCAN)).toEqual(line);
  });
});

describe('nudgeOfKey', () => {
  it('moves one pixel for an arrow and ten with Shift', () => {
    expect(nudgeOfKey('ArrowRight', false)).toEqual({ x: NUDGE_PX, y: 0 });
    expect(nudgeOfKey('ArrowLeft', true)).toEqual({ x: -NUDGE_SHIFT_PX, y: 0 });
    expect(nudgeOfKey('ArrowUp', false)).toEqual({ x: 0, y: -NUDGE_PX });
    expect(nudgeOfKey('ArrowDown', true)).toEqual({ x: 0, y: NUDGE_SHIFT_PX });
  });

  it('ignores a key that is not an arrow', () => {
    expect(nudgeOfKey('a', false)).toBeNull();
  });
});

describe('crossingX', () => {
  it('is the distance of a vertical line from the left edge', () => {
    expect(crossingX(verticalLine(480, SCAN), SCAN.height)).toBe(480);
  });

  it('is the distance at the middle of the height for a slanted line', () => {
    expect(
      crossingX({ start: { x: 400, y: 0 }, end: { x: 600, y: SCAN.height } }, SCAN.height),
    ).toBe(500);
  });
});
