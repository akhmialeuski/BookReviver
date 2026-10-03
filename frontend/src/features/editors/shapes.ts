/**
 * The shapes the page editors draw, and how each is read from and written to the JSON of a stored edit.
 *
 * A stored edit keeps its shape as a map of numbers. The editors work on typed shapes, so each kind has a reader that
 * returns null for a map that does not fit it, and a writer that makes the map the server expects. A new editor adds its
 * shape to {@link EditorShapes}, and the registry then asks for its definition.
 */

import { PAGES_OF, SplitChoice } from '@/features/processing/split';

/** A point in pixels, from the top left corner of an image. */
export interface Point {
  x: number;
  y: number;
}

/** The size of an image in pixels. */
export interface Size {
  width: number;
  height: number;
}

/** The cut of a spread: a straight line through two points, the first near the top of the scan. */
export interface LineShape {
  start: Point;
  end: Point;
}

/** An angle in degrees, counter-clockwise, that the page is turned by. */
export interface RotationShape {
  degrees: number;
}

/** How many pages a scan becomes, as a choice stores it: 1 keeps the scan whole, 2 cuts it. */
export type SplitPages = (typeof PAGES_OF)[SplitChoice];

/** The choice of a reader for a scan: how many pages it becomes, and the cut they drew for two pages, if any. */
export interface SplitShape {
  pages: SplitPages;
  /** The cut drawn for a scan of two pages, or null to cut where the gutter is found. */
  line: LineShape | null;
}

/** The four corners of a sheet of paper, as the server stores a quadrilateral. */
export interface QuadShape {
  topLeft: Point;
  topRight: Point;
  bottomRight: Point;
  bottomLeft: Point;
}

/** An axis-aligned frame, such as the frame of the content of a page. */
export interface RectShape {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * The curves a page is dewarped along: rows of nodes from the top of the page, each row the nodes of one curve from the
 * left. Two rows are the top curve and the bottom curve, and more are the full grid.
 */
export interface MeshShape {
  rows: Point[][];
}

/** The shape of each editor that has a component, by the name of its kind. */
export interface EditorShapes {
  line: LineShape;
  rotation: RotationShape;
  split: SplitShape;
  quad: QuadShape;
  rect: RectShape;
  mesh: MeshShape;
}

/** The kinds of editor that have a component. */
export type EditableKind = keyof EditorShapes;

/** The shape of a stored edit as the API serves it. */
export type Geometry = Readonly<Record<string, unknown>>;

function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function recordOf(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function pointOf(value: unknown): Point | null {
  const record = recordOf(value);
  const x = numberOf(record?.x);
  const y = numberOf(record?.y);
  return x === null || y === null ? null : { x, y };
}

/** Read a line from the geometry of an edit, or null when the geometry is not a line. */
export function readLine(geometry: Geometry | null): LineShape | null {
  const start = pointOf(geometry?.start);
  const end = pointOf(geometry?.end);
  return start === null || end === null ? null : { start, end };
}

/** Write a line the way the server reads it. */
export function writeLine(line: LineShape): Geometry {
  return {
    start: { x: line.start.x, y: line.start.y },
    end: { x: line.end.x, y: line.end.y },
  };
}

/**
 * Read the choice of pages from the geometry of an edit.
 *
 * @param geometry The geometry of the stored edit.
 * @returns The choice, or null when the geometry has no valid number of pages or holds a cut that is not a line.
 */
export function readSplit(geometry: Geometry | null): SplitShape | null {
  const pages = numberOf(geometry?.pages);
  if (pages !== PAGES_OF[SplitChoice.One] && pages !== PAGES_OF[SplitChoice.Two]) {
    return null;
  }
  const stored = geometry?.line ?? null;
  if (stored === null) {
    return { pages, line: null };
  }
  const line = readLine(recordOf(stored));
  return line === null ? null : { pages, line };
}

/** Write a choice of pages the way the server reads it: the cut is null when none was drawn. */
export function writeSplit(split: SplitShape): Geometry {
  return { pages: split.pages, line: split.line === null ? null : writeLine(split.line) };
}

/** Read a rotation from the geometry of an edit, or null when the geometry is not a rotation. */
export function readRotation(geometry: Geometry | null): RotationShape | null {
  const degrees = numberOf(geometry?.degrees);
  return degrees === null ? null : { degrees };
}

/** Write a rotation the way the server reads it. */
export function writeRotation(rotation: RotationShape): Geometry {
  return { degrees: rotation.degrees };
}

/** Read a quadrilateral from the geometry of an edit, or null when the geometry is not one. */
export function readQuad(geometry: Geometry | null): QuadShape | null {
  const topLeft = pointOf(geometry?.top_left);
  const topRight = pointOf(geometry?.top_right);
  const bottomRight = pointOf(geometry?.bottom_right);
  const bottomLeft = pointOf(geometry?.bottom_left);
  return topLeft === null || topRight === null || bottomRight === null || bottomLeft === null
    ? null
    : { topLeft, topRight, bottomRight, bottomLeft };
}

/** Write a quadrilateral the way the server reads it. */
export function writeQuad(quad: QuadShape): Geometry {
  return {
    top_left: { x: quad.topLeft.x, y: quad.topLeft.y },
    top_right: { x: quad.topRight.x, y: quad.topRight.y },
    bottom_right: { x: quad.bottomRight.x, y: quad.bottomRight.y },
    bottom_left: { x: quad.bottomLeft.x, y: quad.bottomLeft.y },
  };
}

/** Read a frame from the geometry of an edit, or null when the geometry is not one or has no area. */
export function readRect(geometry: Geometry | null): RectShape | null {
  const left = numberOf(geometry?.left);
  const top = numberOf(geometry?.top);
  const width = numberOf(geometry?.width);
  const height = numberOf(geometry?.height);
  return left === null ||
    top === null ||
    width === null ||
    height === null ||
    width <= 0 ||
    height <= 0
    ? null
    : { left, top, width, height };
}

/** Write a frame the way the server reads it. */
export function writeRect(rect: RectShape): Geometry {
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

/** The fewest rows a mesh has, the top curve and the bottom curve, and the fewest nodes of a row. */
const MESH_MIN_NODES = 2;

/**
 * Read a mesh from the geometry of an edit.
 *
 * @param geometry The geometry of the stored edit.
 * @returns The mesh, or null when the geometry is not a grid of at least two rows of the same number of at least two
 * nodes.
 */
export function readMesh(geometry: Geometry | null): MeshShape | null {
  const stored = geometry?.rows;
  if (!Array.isArray(stored) || stored.length < MESH_MIN_NODES) {
    return null;
  }
  const rows: Point[][] = [];
  for (const storedRow of stored) {
    if (!Array.isArray(storedRow) || storedRow.length < MESH_MIN_NODES) {
      return null;
    }
    const row = storedRow.map(pointOf);
    if (row.some((node) => node === null) || row.length !== (rows[0]?.length ?? row.length)) {
      return null;
    }
    rows.push(row as Point[]);
  }
  return { rows };
}

/** Write a mesh the way the server reads it. */
export function writeMesh(mesh: MeshShape): Geometry {
  return { rows: mesh.rows.map((row) => row.map((node) => ({ x: node.x, y: node.y }))) };
}

/** Write the name of a shape's part in the words of an attribute: `topLeft` as `top-left`. */
export function dashed(name: string): string {
  return name.replaceAll(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
}
