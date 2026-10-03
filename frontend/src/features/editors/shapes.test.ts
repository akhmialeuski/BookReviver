import { describe, expect, it } from 'vitest';
import {
  dashed,
  readLine,
  readMesh,
  readQuad,
  readRect,
  readRotation,
  readSplit,
  writeLine,
  writeMesh,
  writeQuad,
  writeRect,
  writeRotation,
  writeSplit,
} from '@/features/editors/shapes';

/** Reading the shape of a stored edit, and writing it back as the server reads it. */

describe('readMesh', () => {
  const rows = [
    [
      { x: 0, y: 100 },
      { x: 500, y: 120 },
      { x: 1000, y: 160 },
    ],
    [
      { x: 0, y: 900 },
      { x: 500, y: 930 },
      { x: 1000, y: 980 },
    ],
  ];

  it('reads the rows of nodes of a stored mesh', () => {
    expect(readMesh({ rows })).toEqual({ rows });
  });

  it.each([
    ['no geometry', null],
    ['a frame', { left: 0, top: 0, width: 1, height: 1 }],
    ['a single row', { rows: [rows[0]] }],
    ['rows of different lengths', { rows: [rows[0], rows[1]?.slice(1)] }],
    ['a node that lacks a coordinate', { rows: [[{ x: 1 }, { x: 2, y: 3 }], rows[1]] }],
    ['a row that is no list', { rows: [rows[0], 'row'] }],
  ])('refuses %s', (_name, geometry) => {
    expect(readMesh(geometry)).toBeNull();
  });

  it('is undone by writeMesh', () => {
    expect(readMesh(writeMesh({ rows }))).toEqual({ rows });
  });
});

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

describe('readQuad', () => {
  const stored = {
    top_left: { x: 1, y: 2 },
    top_right: { x: 101, y: 3 },
    bottom_right: { x: 99, y: 203 },
    bottom_left: { x: 0, y: 200 },
  };

  it('reads the four corners of a stored quadrilateral', () => {
    expect(readQuad(stored)).toEqual({
      topLeft: { x: 1, y: 2 },
      topRight: { x: 101, y: 3 },
      bottomRight: { x: 99, y: 203 },
      bottomLeft: { x: 0, y: 200 },
    });
  });

  it.each([
    ['no geometry', null],
    ['a frame', { left: 1, top: 2, width: 3, height: 4 }],
    ['a corner that is missing', { ...stored, bottom_left: undefined }],
  ])('refuses %s', (_name, geometry) => {
    expect(readQuad(geometry)).toBeNull();
  });

  it('is undone by writeQuad, which writes the names the server reads', () => {
    expect(writeQuad(readQuad(stored) ?? emptyQuad())).toEqual(stored);
  });
});

describe('readRect', () => {
  it('reads a stored frame', () => {
    expect(readRect({ left: 10, top: 20, width: 30, height: 40 })).toEqual({
      left: 10,
      top: 20,
      width: 30,
      height: 40,
    });
  });

  it.each([
    ['no geometry', null],
    ['a rotation', { degrees: 1 }],
    ['a frame with no area', { left: 10, top: 20, width: 0, height: 40 }],
  ])('refuses %s', (_name, geometry) => {
    expect(readRect(geometry)).toBeNull();
  });

  it('is undone by writeRect', () => {
    const frame = { left: 1, top: 2, width: 3, height: 4 };

    expect(readRect(writeRect(frame))).toEqual(frame);
  });
});

describe('dashed', () => {
  it('writes the name of a part in the words of an attribute', () => {
    expect(dashed('topLeft')).toBe('top-left');
    expect(dashed('right')).toBe('right');
  });
});

function emptyQuad() {
  const origin = { x: 0, y: 0 };
  return { topLeft: origin, topRight: origin, bottomRight: origin, bottomLeft: origin };
}
