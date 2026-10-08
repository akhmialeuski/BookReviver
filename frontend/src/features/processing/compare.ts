import type { PageVersionSchema, StagePageSchema } from '@/api';
import type { WorldRect } from '@/features/viewer/layout';

/**
 * The two pictures a before-and-after compare puts side by side, and the arithmetic of the swipe.
 *
 * The picture before is the picture of the page that the server gives with its row, and the picture after is what the
 * open step made, or the result of the stage. A result with its tile pyramid cut is drawn from the
 * pyramid, and one without is drawn from its plain preview image, so the canvas never points at tiles that do not exist
 * yet.
 */

/** Where the pixels of a picture are read from. */
export const SourceKind = {
  /** The `info.json` of an IIIF tile pyramid. */
  Iiif: 'iiif',
  /** One plain image. */
  Image: 'image',
} as const;

/** One kind of source (derived from {@link SourceKind}). */
export type SourceKind = (typeof SourceKind)[keyof typeof SourceKind];

/** A picture the canvas can draw. */
export interface ImageSource {
  kind: SourceKind;
  url: string;
}

/** The two pictures of a compare; a side the page has no picture for is null. */
export interface ComparePair {
  before: ImageSource | null;
  after: ImageSource | null;
}

/** Give the picture of a result of a stage: its pyramid once cut, else its plain preview image. */
export function sourceOfResult(version: PageVersionSchema | null | undefined): ImageSource | null {
  const images = version?.images;
  if (version === null || version === undefined || images === null || images === undefined) {
    return null;
  }
  return version.tiles_ready
    ? { kind: SourceKind.Iiif, url: images.iiif_info }
    : { kind: SourceKind.Image, url: images.preview };
}

/** Give the picture of a preview, which is a plain image of the preview size. */
export function sourceOfPreview(version: PageVersionSchema | null | undefined): ImageSource | null {
  return version?.preview === null || version?.preview === undefined
    ? null
    : { kind: SourceKind.Image, url: version.preview };
}

/** Tell whether two sources draw the same image. */
export function sameImage(a: ImageSource | null, b: ImageSource | null): boolean {
  return a !== null && b !== null && a.kind === b.kind && a.url === b.url;
}

/**
 * Give the two pictures of a compare for a page.
 *
 * The picture before is the picture of the page, which is what the strip shows too: what the open step reads, else what
 * the stage reads. The picture after is what the open step made, or, with no step open, the result of the stage.
 *
 * @param picture The picture of the page, which is the version the strip shows.
 * @param row The row of the page, asked for the open step when one is open, or undefined while it loads.
 */
export function comparePairOf(
  picture: PageVersionSchema | null,
  row: StagePageSchema | undefined,
): ComparePair {
  const made = row?.step === null || row?.step === undefined ? row?.version : row.step.version;
  return {
    before: sourceOfResult(picture),
    after: sourceOfResult(made),
  };
}

/**
 * Work out how much of the picture before a swipe uncovers, in the pixels of that picture.
 *
 * The divider stands at a place of the screen, so the clip follows the view when the reader pans and zooms: it is the
 * part of the picture that lies to the left of the divider.
 *
 * @param dividerX Where the divider stands, in viewport coordinates.
 * @param pictureLeft The left edge of the picture in viewport coordinates.
 * @param pictureWidth The width of the picture in viewport coordinates.
 * @param pixelWidth The width of the picture in its own pixels.
 * @returns The width of the clip in pixels, from 0 to the whole picture.
 */
export function clipWidth(
  dividerX: number,
  pictureLeft: number,
  pictureWidth: number,
  pixelWidth: number,
): number {
  if (pictureWidth <= 0) {
    return 0;
  }
  const fraction = Math.min(Math.max((dividerX - pictureLeft) / pictureWidth, 0), 1);
  return fraction * pixelWidth;
}

/** The position of the divider as a share of the canvas, from 0 to 1. */
export const DIVIDER_CENTRE = 0.5;

/** Keep the divider on the canvas, so it can always be taken hold of. */
export function clampDivider(share: number): number {
  const margin = 0.02;
  return Math.min(Math.max(share, margin), 1 - margin);
}

/** Which picture of a compare stands whole, and so which one lies inside the other. */
export const Side = {
  Before: 'before',
  After: 'after',
} as const;

/** One side of a compare (derived from {@link Side}). */
export type Side = (typeof Side)[keyof typeof Side];

/**
 * Where two pictures of a compare stand in one world.
 *
 * The base picture is drawn whole at the origin, as tall as a page, and the other picture lies inside it, in the place
 * its pixels have there. Both numbers of the place are shares of the base picture, so they do not depend on the
 * resolution either picture is drawn at.
 */
export interface PairPlacement {
  base: Side;
  /** The left edge of the other picture, as a share of the width of the base picture. */
  left: number;
  /** The top edge of the other picture, as a share of the height of the base picture. */
  top: number;
  /** The width of the other picture, as a share of the width of the base picture. */
  width: number;
  /** The height of the other picture, as a share of the height of the base picture. */
  height: number;
}

/** The numbers of a 3 by 3 matrix in rows that a scale and a shift fill: x scale, x shift, y scale, y shift. */
const MATRIX_X_SCALE = 0;
const MATRIX_X_SHIFT = 2;
const MATRIX_Y_SCALE = 4;
const MATRIX_Y_SHIFT = 5;
const MATRIX_SIZE = 9;
/** Matrix entries that are 0 in a scale and a shift, and 1 in the corner. */
const MATRIX_ZEROS = [1, 3, 6, 7] as const;
const MATRIX_CORNER = 8;
/** How far an entry may be from its exact value and still be taken for it. */
const MATRIX_TOLERANCE = 1e-6;
/** How little of the side of a picture a shift or a size may differ by before the pictures are drawn alike. */
const SAME_SHARE = 1e-3;

function numberOf(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

/**
 * Work out how many pixels of the image a step read make a pixel of the full image.
 *
 * A result of a full run is in full pixels. A preview of the margins is in the pixels of a shrunk page, whose size over the
 * size of the full page the step records. The transform of any other step of a preview has no such record, so it has none.
 */
function pixelRatio(
  version: Pick<PageVersionSchema, 'scale' | 'transform' | 'data'>,
): number | null {
  if (version.scale === 'full') {
    return 1;
  }
  const shrunk = numberOf(version.data.width_px);
  const full = numberOf(version.data.source_width_px);
  return version.transform.kind === 'place' && shrunk !== null && full !== null
    ? shrunk / full
    : null;
}

/**
 * Work out where the two pictures of a step stand in one world, from the transform the result carries.
 *
 * The transform is read, not worked out again: a step that shifts and scales its input onto the picture it makes, such as
 * the margins that put a block of text on a page of the book, or a cut of old, gives the matrix from the pixels of the
 * input to the pixels of the result. The picture that is the larger of the two stands whole and the other lies inside it.
 * A step that leaves the picture as it is, or that turns, bends or reshapes it, gives no place, so the two are drawn as tall
 * as each other.
 *
 * @param version The result of the step, with its transform and the sizes it records, or null for none.
 * @param input The version the step read, whose data holds the size of its image, or null for none.
 * @returns The place, or null when the pictures are drawn as tall as each other.
 */
export function placementOf(
  version: Pick<PageVersionSchema, 'scale' | 'transform' | 'data'> | null | undefined,
  input: Pick<PageVersionSchema, 'data'> | null | undefined,
): PairPlacement | null {
  const { matrix, kind } = version?.transform ?? {};
  if (
    version === null ||
    version === undefined ||
    input === null ||
    input === undefined ||
    (kind !== 'place' && kind !== 'crop') ||
    matrix === null ||
    matrix === undefined ||
    matrix.length !== MATRIX_SIZE ||
    MATRIX_ZEROS.some((index) => Math.abs(matrix[index] ?? 1) > MATRIX_TOLERANCE) ||
    Math.abs((matrix[MATRIX_CORNER] ?? 0) - 1) > MATRIX_TOLERANCE
  ) {
    return null;
  }
  const ratio = pixelRatio(version);
  const scaleX = matrix[MATRIX_X_SCALE] ?? 0;
  const scaleY = matrix[MATRIX_Y_SCALE] ?? 0;
  const inputWidth = numberOf(input.data.width_px);
  const inputHeight = numberOf(input.data.height_px);
  const width = numberOf(version.data.width_px);
  const height = numberOf(version.data.height_px);
  if (
    ratio === null ||
    inputWidth === null ||
    inputHeight === null ||
    width === null ||
    height === null ||
    scaleX <= 0 ||
    scaleY <= 0
  ) {
    return null;
  }
  // The input in the result, as shares of the result
  const inside = {
    left: (matrix[MATRIX_X_SHIFT] ?? 0) / width,
    top: (matrix[MATRIX_Y_SHIFT] ?? 0) / height,
    width: (scaleX * inputWidth * ratio) / width,
    height: (scaleY * inputHeight * ratio) / height,
  };
  if (
    Math.abs(inside.left) < SAME_SHARE &&
    Math.abs(inside.top) < SAME_SHARE &&
    Math.abs(inside.width - 1) < SAME_SHARE &&
    Math.abs(inside.height - 1) < SAME_SHARE
  ) {
    return null;
  }
  if (inside.width <= 1 && inside.height <= 1) {
    return { base: Side.After, ...inside };
  }
  // The result is the smaller one, so the same numbers are read the other way round: the result in the input
  return {
    base: Side.Before,
    left: -inside.left / inside.width,
    top: -inside.top / inside.height,
    width: 1 / inside.width,
    height: 1 / inside.height,
  };
}

/**
 * Give the rectangle of the world that the picture lying inside the base one covers.
 *
 * @param placement Where the pictures stand.
 * @param base The rectangle of the base picture in the world.
 */
export function innerRect(placement: PairPlacement, base: WorldRect): WorldRect {
  return {
    x: base.x + placement.left * base.width,
    y: base.y + placement.top * base.height,
    width: placement.width * base.width,
    height: placement.height * base.height,
  };
}
