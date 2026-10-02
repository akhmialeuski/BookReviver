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

/** The shape of each editor that has a component, by the name of its kind. */
export interface EditorShapes {
  line: LineShape;
  rotation: RotationShape;
  split: SplitShape;
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
