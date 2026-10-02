import type { PageSchema, ScanSchema } from '@/api';
import { Picture } from '@/features/editors/types';
import { type ImageSource, SourceKind } from '@/features/processing/compare';

/**
 * Find the picture an editor lies on.
 *
 * @param picture Which picture the editor wants.
 * @param scan The scan the open page was cut from, if any.
 * @param page The open page.
 * @param before The picture before the stage on the page, if any.
 * @returns The picture, or null when it is not there yet: a scan whose tiles are not cut, or a page without an image.
 */
export function pictureOf(
  picture: Picture,
  scan: ScanSchema | null,
  page: PageSchema,
  before: ImageSource | null,
): ImageSource | null {
  if (picture === Picture.Scan) {
    const images = scan?.images ?? null;
    return images === null ? null : { kind: SourceKind.Iiif, url: images.iiif_info };
  }
  // The picture the step reads is the result of the stage before, else the image the page has now
  return (
    before ?? (page.images === null ? null : { kind: SourceKind.Iiif, url: page.images.iiif_info })
  );
}
