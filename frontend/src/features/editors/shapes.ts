/**
 * The shapes the page editors draw, and how each is read from and written to the JSON of a stored edit.
 *
 * A stored edit keeps its shape as a map of numbers. The editors work on typed shapes, so each kind has a reader that
 * returns null for a map that does not fit it, and a writer that makes the map the server expects. A new editor adds its
 * shape to {@link EditorShapes}, and the registry then asks for its definition.
 */

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

/** The shape of each editor that has a component, by the name of its kind. */
export interface EditorShapes {
  line: LineShape;
  rotation: RotationShape;
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

/** Read a rotation from the geometry of an edit, or null when the geometry is not a rotation. */
export function readRotation(geometry: Geometry | null): RotationShape | null {
  const degrees = numberOf(geometry?.degrees);
  return degrees === null ? null : { degrees };
}

/** Write a rotation the way the server reads it. */
export function writeRotation(rotation: RotationShape): Geometry {
  return { degrees: rotation.degrees };
}
