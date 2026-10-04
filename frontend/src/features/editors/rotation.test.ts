import { describe, expect, it } from 'vitest';
import {
  ANGLE_LIMIT,
  AxisEnd,
  angleFromAxisPointer,
  angleFromPointer,
  axisEndAt,
  formatAngle,
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

describe('axisEndAt', () => {
  it('lays the axis level for no angle, a handle at each end', () => {
    const right = axisEndAt(CENTRE, RADIUS, 0, 0, AxisEnd.Right);
    const left = axisEndAt(CENTRE, RADIUS, 0, 0, AxisEnd.Left);

    expect(right.x).toBeCloseTo(CENTRE.x + RADIUS, PRECISION);
    expect(right.y).toBeCloseTo(CENTRE.y, PRECISION);
    expect(left.x).toBeCloseTo(CENTRE.x - RADIUS, PRECISION);
    expect(left.y).toBeCloseTo(CENTRE.y, PRECISION);
  });

  it('raises the right end and lowers the left for a counter-clockwise angle', () => {
    const right = axisEndAt(CENTRE, RADIUS, 10, 0, AxisEnd.Right);
    const left = axisEndAt(CENTRE, RADIUS, 10, 0, AxisEnd.Left);

    expect(right.y).toBeLessThan(CENTRE.y);
    expect(left.y).toBeGreaterThan(CENTRE.y);
    // The two ends and the middle lie on one line
    expect(right.x - CENTRE.x).toBeCloseTo(CENTRE.x - left.x, PRECISION);
    expect(CENTRE.y - right.y).toBeCloseTo(left.y - CENTRE.y, PRECISION);
  });
});

describe('angleFromAxisPointer', () => {
  it.each([AxisEnd.Left, AxisEnd.Right])(
    'reads back the angle the %s end was put at, through a turned view too',
    (end) => {
      for (const [degrees, view] of [
        [-30, 0],
        [-2.4, 0],
        [0, 0],
        [12.5, 0],
        [7.3, 25],
      ] as const) {
        const handle = axisEndAt(CENTRE, RADIUS, degrees, view, end);

        expect(angleFromAxisPointer(CENTRE, handle, view, end)).toBeCloseTo(degrees, 1);
      }
    },
  );

  it('stops at the limit when the pointer goes round the page', () => {
    const above = { x: CENTRE.x, y: CENTRE.y - RADIUS };

    expect(angleFromAxisPointer(CENTRE, above, 0, AxisEnd.Right)).toBe(ANGLE_LIMIT);
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

describe('formatAngle', () => {
  it('writes up to a hundredth and drops trailing zeros', () => {
    expect(formatAngle(-2.4)).toBe('-2.4');
    expect(formatAngle(0)).toBe('0');
    expect(formatAngle(1.05)).toBe('1.05');
    expect(formatAngle(0.1 + 0.2)).toBe('0.3');
  });
});

describe('stepByKey', () => {
  it('adds 0.05 for right and up and takes it for left and down', () => {
    expect(stepByKey(1, 'ArrowRight', false)).toBe(1.05);
    expect(stepByKey(1, 'ArrowUp', false)).toBe(1.05);
    expect(stepByKey(1, 'ArrowLeft', false)).toBe(0.95);
    expect(stepByKey(1, 'ArrowDown', false)).toBe(0.95);
  });

  it('does not accumulate the error of floating point over many presses', () => {
    let angle = 0;
    for (let press = 0; press < 20; press += 1) {
      angle = stepByKey(angle, 'ArrowRight', false) ?? angle;
    }
    expect(angle).toBe(1);
  });

  it('steps by a whole degree with Shift and stays within the limit', () => {
    expect(stepByKey(1, 'ArrowRight', true)).toBe(2);
    expect(stepByKey(ANGLE_LIMIT, 'ArrowRight', true)).toBe(ANGLE_LIMIT);
  });

  it('ignores a key that is not an arrow', () => {
    expect(stepByKey(1, 'a', false)).toBeNull();
  });
});
