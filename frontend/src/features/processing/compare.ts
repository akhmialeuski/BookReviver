import type { PageSchema, PageVersionSchema, StagePageSchema } from '@/api';

/**
 * The two pictures a before-and-after compare puts side by side, and the arithmetic of the swipe.
 *
 * The picture before is the current result of the stage that came before on the page, and the picture after is the
 * current result of this stage or the preview. A result with its tile pyramid cut is drawn from the pyramid, and one
 * without is drawn from its plain preview image, so the canvas never points at tiles that do not exist yet.
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

/**
 * Find the picture before a stage on a page: the result of the nearest earlier stage that has one, else, for a page the
 * stage has not made a picture of yet, the picture the page has now, which is what the stage would read.
 *
 * @param page The page.
 * @param own The row of the page in the stage that is open.
 * @param earlier The rows of the earlier stages that have processors, the nearest first, each by page identifier.
 * @returns The picture, or null when nothing came before the stage, as on the Split stage.
 */
export function beforeSourceOf(
  page: PageSchema,
  own: StagePageSchema | undefined,
  earlier: ReadonlyArray<ReadonlyMap<string, StagePageSchema>>,
): ImageSource | null {
  for (const rows of earlier) {
    const source = sourceOfResult(rows.get(page.id)?.version);
    if (source !== null) {
      return source;
    }
  }
  if (earlier.length === 0 || sourceOfResult(own?.version) !== null) {
    return null;
  }
  return page.images === null ? null : { kind: SourceKind.Iiif, url: page.images.iiif_info };
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
