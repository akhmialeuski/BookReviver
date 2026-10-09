import type { PageVersionSchema, ScanSchema } from '@/api';
import { Picture } from '@/features/editors/types';
import { type ImageSource, SourceKind, sourceOfResult } from '@/features/processing/compare';

/**
 * Find the picture an editor lies on.
 *
 * @param picture Which picture the editor wants.
 * @param scan The scan the open page was cut from, if any.
 * @param before The picture the server gives for the page, which is what the open step reads, if any.
 * @param made The version the step of the editor made, which is the picture of an editor that lies on the page its step made.
 * @returns The picture, or null when it is not there yet: a scan whose tiles are not cut, or a page without an image.
 */
export function pictureOf(
  picture: Picture,
  scan: ScanSchema | null,
  before: ImageSource | null,
  made: PageVersionSchema | null = null,
): ImageSource | null {
  if (picture === Picture.Scan) {
    const images = scan?.images ?? null;
    return images === null ? null : { kind: SourceKind.Iiif, url: images.iiif_info };
  }
  if (picture === Picture.Output) {
    return sourceOfResult(made);
  }
  // The server works out what the step reads for the row of the page, and the strip and the canvas draw it too; there is
  // none while the row loads, and never the latest image of the page, which a later stage made
  return before;
}
