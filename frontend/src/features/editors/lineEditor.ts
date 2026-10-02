import { LineCanvas } from '@/features/editors/LineCanvas';
import { LinePanel } from '@/features/editors/LinePanel';
import { verticalLine } from '@/features/editors/line';
import { type LineShape, readLine, writeLine } from '@/features/editors/shapes';
import { type EditorDefinition, type PageContext, Picture } from '@/features/editors/types';
import { isSplit, pagesOfScan } from '@/features/processing/split';

/**
 * The split line editor: the cut of a spread drawn on the scan.
 *
 * The edit belongs to the left page of the scan, which is the page that runs the cut, whichever half is open. The line is
 * in the pixels of the scan, and until the reader moves it the line stands where the step found the cut.
 */

function scanPagesOf({ current, items }: PageContext) {
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
  owner: (context) => scanPagesOf(context)[0] ?? context.current.page,
  size: ({ scan }) =>
    scan === null ? null : { width: scan.facts.width_px, height: scan.facts.height_px },
  // A scan kept whole is cut only when the reader asks for two pages, so moving its line must not cut it
  runsAfterEdit: (context) => isSplit(scanPagesOf(context)),
  fallback: ({ size, result }) => {
    const box = size ?? { width: 0, height: 0 };
    return verticalLine(result?.cutX ?? box.width / 2, box);
  },
  read: readLine,
  write: writeLine,
  Canvas: LineCanvas,
  Panel: LinePanel,
};
