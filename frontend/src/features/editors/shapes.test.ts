import { describe, expect, it } from 'vitest';
import { readLine, readRotation, writeLine, writeRotation } from '@/features/editors/shapes';

/** Reading the shape of a stored edit, and writing it back as the server reads it. */

describe('readLine', () => {
  it('reads the two points of a stored line', () => {
    const geometry = { start: { x: 10, y: 0 }, end: { x: 12.5, y: 300 } };

    expect(readLine(geometry)).toEqual({ start: { x: 10, y: 0 }, end: { x: 12.5, y: 300 } });
  });

  it.each([
    ['no geometry', null],
    ['a rotation', { degrees: 1 }],
    ['a point that lacks a coordinate', { start: { x: 1 }, end: { x: 2, y: 3 } }],
    ['a coordinate that is not a number', { start: { x: 'a', y: 0 }, end: { x: 2, y: 3 } }],
  ])('refuses %s', (_name, geometry) => {
    expect(readLine(geometry)).toBeNull();
  });

  it('is undone by writeLine', () => {
    const line = { start: { x: 1, y: 2 }, end: { x: 3, y: 4 } };

    expect(readLine(writeLine(line))).toEqual(line);
  });
});

describe('readRotation', () => {
  it('reads the angle of a stored rotation', () => {
    expect(readRotation({ degrees: -2.4 })).toEqual({ degrees: -2.4 });
  });

  it('refuses a line and a missing angle', () => {
    expect(readRotation({ start: { x: 0, y: 0 }, end: { x: 1, y: 1 } })).toBeNull();
    expect(readRotation(null)).toBeNull();
    expect(readRotation({ degrees: Number.NaN })).toBeNull();
  });

  it('is undone by writeRotation', () => {
    expect(readRotation(writeRotation({ degrees: 1.5 }))).toEqual({ degrees: 1.5 });
  });
});
