import { LineCanvas } from '@/features/editors/LineCanvas';
import { cutLine } from '@/features/editors/line';
import { type LineShape, readLine, writeLine } from '@/features/editors/shapes';
import { type EditorDefinition, type PageContext, Picture } from '@/features/editors/types';
import { isSplit, pagesOfScan } from '@/features/processing/split';
import { MESSAGES } from '@/shared/messages';

/**
 * The split line editor: the cut of a spread drawn on the scan, for the recipe that cuts every scan it is given.
 *
 * The edit belongs to the left page of the scan, which is the page that runs the cut, whichever half is open. The line is
 * in the pixels of the scan, and until the reader moves it the line stands where the step found the cut.
 */

/** Give the pages cut from the scan of the open page, in the order of their slots. */
export function scanPagesOf({ current, items }: PageContext) {
  return current.page.scan_id === null
    ? []
    : pagesOfScan(
        items.map((item) => item.page),
        current.page.scan_id,
      );
}

export const lineEditor: EditorDefinition<LineShape> = {
  picture: Picture.Scan,
  alwaysOn: true,
  needsResult: false,
  owner: (context) => scanPagesOf(context)[0] ?? context.current.page,
  size: ({ scan }) =>
    scan === null ? null : { width: scan.facts.width_px, height: scan.facts.height_px },
  // A scan kept whole is cut only when the reader asks for two pages, so moving its line must not cut it
  runsAfterEdit: (context) => isSplit(scanPagesOf(context)),
  fallback: ({ size, result }) => cutLine(result, size ?? { width: 0, height: 0 }),
  read: readLine,
  write: writeLine,
  describe: ({ start, end }) =>
    MESSAGES.processing.timeline.hand.line(
      Math.round(start.x),
      Math.round(start.y),
      Math.round(end.x),
      Math.round(end.y),
    ),
  Canvas: LineCanvas,
};
