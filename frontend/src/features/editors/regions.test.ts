import { describe, expect, it } from 'vitest';
import { addZone, boundsOf, moveCorner, newZone, removeZone } from '@/features/editors/regions';
import { readRegions, writeRegions, ZoneMode } from '@/features/editors/shapes';

const SIZE = { width: 1000, height: 2000 };

describe('newZone', () => {
  it('stands a rectangle of two fifths of the page in the middle of it', () => {
    const zone = newZone(ZoneMode.Add, SIZE);

    expect(zone.mode).toBe('add');
    expect(zone.points).toEqual([
      { x: 300, y: 600 },
      { x: 700, y: 600 },
      { x: 700, y: 1400 },
      { x: 300, y: 1400 },
    ]);
  });
});

describe('addZone and removeZone', () => {
  it('puts a new zone after the others and takes one away by its place', () => {
    const first = newZone(ZoneMode.Add, SIZE);
    const second = newZone(ZoneMode.Remove, SIZE);
    const both = addZone(addZone({ zones: [] }, first), second);

    expect(both.zones).toEqual([first, second]);
    expect(removeZone(both, 0).zones).toEqual([second]);
  });
});

describe('moveCorner', () => {
  const regions = { zones: [newZone(ZoneMode.Add, SIZE), newZone(ZoneMode.Remove, SIZE)] };

  it('moves one corner of one zone and leaves the rest as they were', () => {
    const moved = moveCorner(regions, 1, 2, { x: 800, y: 1500 }, SIZE);

    expect(moved.zones[1]?.points[2]).toEqual({ x: 800, y: 1500 });
    expect(moved.zones[1]?.points[0]).toEqual(regions.zones[1]?.points[0]);
    expect(moved.zones[0]).toBe(regions.zones[0]);
  });

  it('holds the corner on the page', () => {
    const moved = moveCorner(regions, 0, 0, { x: -50, y: 9999 }, SIZE);

    expect(moved.zones[0]?.points[0]).toEqual({ x: 0, y: 2000 });
  });
});

describe('boundsOf', () => {
  it('gives the box a zone fills', () => {
    expect(boundsOf(newZone(ZoneMode.Add, SIZE).points)).toEqual([300, 600, 400, 800]);
  });
});

describe('the zones as the server stores them', () => {
  it('survive a write and a read', () => {
    const regions = { zones: [newZone(ZoneMode.Add, SIZE), newZone(ZoneMode.Remove, SIZE)] };

    expect(readRegions(writeRegions(regions))).toEqual(regions);
  });

  it.each([
    ['no list', {}],
    [
      'a zone of two points',
      {
        zones: [
          {
            mode: 'add',
            points: [
              { x: 1, y: 1 },
              { x: 2, y: 2 },
            ],
          },
        ],
      },
    ],
    [
      'an unknown mode',
      {
        zones: [
          {
            mode: 'hide',
            points: [
              { x: 0, y: 0 },
              { x: 1, y: 0 },
              { x: 1, y: 1 },
            ],
          },
        ],
      },
    ],
    [
      'a point without a number',
      { zones: [{ mode: 'add', points: [{ x: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }] }] },
    ],
  ])('are not read from %s', (_name, geometry) => {
    expect(readRegions(geometry)).toBeNull();
  });

  it('are read as none from an edit that has no zones in it', () => {
    expect(readRegions({ zones: [] })).toEqual({ zones: [] });
    expect(readRegions(null)).toBeNull();
  });
});
