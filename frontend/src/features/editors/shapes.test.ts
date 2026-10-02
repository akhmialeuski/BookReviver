import { describe, expect, it } from 'vitest';
import {
  readLine,
  readRotation,
  readSplit,
  writeLine,
  writeRotation,
  writeSplit,
} from '@/features/editors/shapes';

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

describe('readSplit', () => {
  const line = { start: { x: 10, y: 0 }, end: { x: 12, y: 300 } };

  it('reads two pages with the line a reader drew', () => {
    expect(readSplit({ pages: 2, line })).toEqual({ pages: 2, line });
  });

  it.each([
    ['a choice with a null line', { pages: 1, line: null }, { pages: 1, line: null }],
    ['a choice with no line key', { pages: 2 }, { pages: 2, line: null }],
  ])('reads %s', (_name, geometry, expected) => {
    expect(readSplit(geometry)).toEqual(expected);
  });

  it.each([
    ['no geometry', null],
    ['a line without pages', line],
    ['three pages', { pages: 3, line: null }],
    ['pages that are not a number', { pages: 'two', line: null }],
    ['a cut that is not a line', { pages: 2, line: { start: { x: 1 } } }],
    ['a cut that is a number', { pages: 2, line: 5 }],
  ])('refuses %s', (_name, geometry) => {
    expect(readSplit(geometry)).toBeNull();
  });

  it('is undone by writeSplit, with and without a line', () => {
    expect(readSplit(writeSplit({ pages: 2, line }))).toEqual({ pages: 2, line });
    expect(readSplit(writeSplit({ pages: 1, line: null }))).toEqual({ pages: 1, line: null });
  });

  it('writes the line as null when none was drawn, the way the server stores a choice', () => {
    expect(writeSplit({ pages: 2, line: null })).toEqual({ pages: 2, line: null });
  });
});
