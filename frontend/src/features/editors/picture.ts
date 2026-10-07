import type { PageVersionSchema, ScanSchema } from '@/api';
import { Picture } from '@/features/editors/types';
import { type ImageSource, SourceKind, sourceOfResult } from '@/features/processing/compare';

/**
 * Find the picture an editor lies on.
 *
 * @param picture Which picture the editor wants.
 * @param scan The scan the open page was cut from, if any.
 * @param before The picture before the stage on the page, if any.
 * @param stepInput The version an earlier step of the stage made and the step of the editor reads, if the step is not the
 *                  first of the stage.
 * @param made The version the step of the editor made, which is the picture of an editor that lies on the page its step made.
 * @returns The picture, or null when it is not there yet: a scan whose tiles are not cut, or a page without an image.
 */
export function pictureOf(
  picture: Picture,
  scan: ScanSchema | null,
  before: ImageSource | null,
  stepInput: PageVersionSchema | null = null,
  made: PageVersionSchema | null = null,
): ImageSource | null {
  if (picture === Picture.Scan) {
    const images = scan?.images ?? null;
    return images === null ? null : { kind: SourceKind.Iiif, url: images.iiif_info };
  }
  if (picture === Picture.Output) {
    return sourceOfResult(made);
  }
  // A step after the first reads what the step before it made, and the first reads the picture the server gives for the
  // page; there is none while the row loads, and never the latest image of the page, which a later stage made
  return stepInput === null ? before : sourceOfResult(stepInput);
}
