import { describe, expect, it } from 'vitest';
import {
  CURVE_NODES,
  CURVES,
  curvesOf,
  GRID_ROWS,
  gridOf,
  meshOf,
  moveNode,
  straightMesh,
} from '@/features/editors/mesh';
import { readResult } from '@/features/processing/results';

/** The arithmetic of the curves editor, in the pixels of the image the step reads. */

const SIZE = { width: 1000, height: 1400 };

describe('straightMesh', () => {
  it('lays two straight curves of five nodes near the top and the bottom of the image', () => {
    const mesh = straightMesh(SIZE);

    expect(mesh.rows).toHaveLength(CURVES);
    expect(mesh.rows.map((row) => row.length)).toEqual([CURVE_NODES, CURVE_NODES]);
    expect(mesh.rows[0]?.map((node) => node.x)).toEqual([0, 250, 500, 750, 1000]);
    expect(new Set(mesh.rows[0]?.map((node) => node.y))).toEqual(new Set([140]));
    expect(new Set(mesh.rows[1]?.map((node) => node.y))).toEqual(new Set([1260]));
  });
});

describe('meshOf', () => {
  it('starts from the curves the step found', () => {
    const rows = [
      [
        { x: 0, y: 100 },
        { x: 1000, y: 140 },
      ],
      [
        { x: 0, y: 900 },
        { x: 1000, y: 960 },
      ],
    ];
    const result = readResult({ data: { mesh: { rows } } });

    expect(meshOf(result, SIZE).rows).toEqual(rows);
  });

  it('starts from two straight curves when the step found none', () => {
    expect(meshOf(null, SIZE)).toEqual(straightMesh(SIZE));
    expect(meshOf(readResult({ data: {} }), SIZE)).toEqual(straightMesh(SIZE));
  });
});

describe('curvesOf', () => {
  it('keeps the top row and the bottom row of a grid', () => {
    const grid = gridOf(straightMesh(SIZE));

    expect(curvesOf(grid).rows).toEqual([grid.rows[0], grid.rows.at(-1)]);
  });
});

describe('gridOf', () => {
  it('puts the rows between two curves at even steps from the top one to the bottom one', () => {
    const grid = gridOf(straightMesh(SIZE));

    expect(grid.rows).toHaveLength(GRID_ROWS);
    expect(grid.rows.map((row) => row[0]?.y)).toEqual([140, 420, 700, 980, 1260]);
  });

  it('leaves a mesh that is a grid already as it is', () => {
    const grid = gridOf(straightMesh(SIZE));

    expect(gridOf(grid)).toBe(grid);
  });
});

describe('moveNode', () => {
  it('moves one node to the pointer and no other', () => {
    const moved = moveNode(straightMesh(SIZE), 1, 2, { x: 520, y: 1300 }, SIZE);

    expect(moved.rows[1]?.[2]).toEqual({ x: 520, y: 1300 });
    expect(moved.rows[1]?.[1]).toEqual({ x: 250, y: 1260 });
    expect(moved.rows[0]).toEqual(straightMesh(SIZE).rows[0]);
  });

  it('keeps a node on the image', () => {
    const moved = moveNode(straightMesh(SIZE), 0, 0, { x: -30, y: 2000 }, SIZE);

    expect(moved.rows[0]?.[0]).toEqual({ x: 0, y: 1400 });
  });
});
