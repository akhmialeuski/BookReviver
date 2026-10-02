import { describe, expect, it } from 'vitest';
import {
  ANGLE_LIMIT,
  angleFromPointer,
  handleAt,
  limitAngle,
  parseAngle,
  stepByKey,
  stepByWheel,
} from '@/features/editors/rotation';

/** The arithmetic of the rotation handle and of the field and the wheel that set the same angle. */

const CENTRE = { x: 400, y: 300 };
const RADIUS = 250;
const PRECISION = 6;

describe('handleAt', () => {
  it('stands straight above the middle of the page for no angle', () => {
    const handle = handleAt(CENTRE, RADIUS, 0, 0);

    expect(handle.x).toBeCloseTo(CENTRE.x, PRECISION);
    expect(handle.y).toBeCloseTo(CENTRE.y - RADIUS, PRECISION);
  });

  it('leans to the left for a counter-clockwise angle', () => {
    const handle = handleAt(CENTRE, RADIUS, 90, 0);

    expect(handle.x).toBeCloseTo(CENTRE.x - RADIUS, PRECISION);
    expect(handle.y).toBeCloseTo(CENTRE.y, PRECISION);
  });

  it('follows a turned view', () => {
    // The view is turned a quarter clockwise, so the top of the page is on the right
    const handle = handleAt(CENTRE, RADIUS, 0, 90);

    expect(handle.x).toBeCloseTo(CENTRE.x + RADIUS, PRECISION);
    expect(handle.y).toBeCloseTo(CENTRE.y, PRECISION);
  });
});

describe('angleFromPointer', () => {
  it.each([-30, -2.4, 0, 0.1, 12.5, 44.9])(
    'reads back the angle %s the handle was put at',
    (degrees) => {
      const handle = handleAt(CENTRE, RADIUS, degrees, 0);

      expect(angleFromPointer(CENTRE, handle, 0)).toBeCloseTo(degrees, 1);
    },
  );

  it('reads back the angle through a turned view', () => {
    const handle = handleAt(CENTRE, RADIUS, 7.3, 25);

    expect(angleFromPointer(CENTRE, handle, 25)).toBeCloseTo(7.3, 1);
  });

  it('rounds to a tenth and stops at the limit', () => {
    const below = { x: CENTRE.x + 10, y: CENTRE.y + RADIUS };
    const far = handleAt(CENTRE, RADIUS, 80, 0);

    expect(Math.abs(angleFromPointer(CENTRE, below, 0))).toBe(ANGLE_LIMIT);
    expect(angleFromPointer(CENTRE, far, 0)).toBe(ANGLE_LIMIT);
  });
});

describe('stepByWheel', () => {
  it('adds a tenth of a degree for a notch away from the reader and takes it for a notch towards', () => {
    expect(stepByWheel(1, -100)).toBe(1.1);
    expect(stepByWheel(1, 100)).toBe(0.9);
  });

  it('does not accumulate the error of floating point', () => {
    let angle = 0;
    for (let notch = 0; notch < 30; notch += 1) {
      angle = stepByWheel(angle, -1);
    }
    expect(angle).toBe(3);
  });

  it('stays within the limit', () => {
    expect(stepByWheel(ANGLE_LIMIT, -1)).toBe(ANGLE_LIMIT);
    expect(stepByWheel(-ANGLE_LIMIT, 1)).toBe(-ANGLE_LIMIT);
  });

  it('leaves the angle alone for a wheel that did not turn up or down', () => {
    expect(stepByWheel(2.5, 0)).toBe(2.5);
  });
});

describe('limitAngle and parseAngle', () => {
  it('rounds to a tenth and limits', () => {
    expect(limitAngle(1.26)).toBe(1.3);
    expect(limitAngle(99)).toBe(ANGLE_LIMIT);
  });

  it('reads a typed angle with a point or a comma', () => {
    expect(parseAngle('-2.4')).toBe(-2.4);
    expect(parseAngle(' 3,5 ')).toBe(3.5);
  });

  it('refuses text that is not a number', () => {
    expect(parseAngle('')).toBeNull();
    expect(parseAngle('abc')).toBeNull();
  });
});

describe('stepByKey', () => {
  it('adds a tenth for right and up and takes it for left and down', () => {
    expect(stepByKey(1, 'ArrowRight', false)).toBe(1.1);
    expect(stepByKey(1, 'ArrowUp', false)).toBe(1.1);
    expect(stepByKey(1, 'ArrowLeft', false)).toBe(0.9);
    expect(stepByKey(1, 'ArrowDown', false)).toBe(0.9);
  });

  it('steps by a whole degree with Shift and stays within the limit', () => {
    expect(stepByKey(1, 'ArrowRight', true)).toBe(2);
    expect(stepByKey(ANGLE_LIMIT, 'ArrowRight', true)).toBe(ANGLE_LIMIT);
  });

  it('ignores a key that is not an arrow', () => {
    expect(stepByKey(1, 'a', false)).toBeNull();
  });
});
