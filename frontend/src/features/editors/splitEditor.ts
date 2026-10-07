import { LinePanel } from '@/features/editors/LinePanel';
import { cutLine } from '@/features/editors/line';
import { lineEditor } from '@/features/editors/lineEditor';
import { SplitCanvas } from '@/features/editors/SplitCanvas';
import { readSplit, type SplitShape, writeSplit } from '@/features/editors/shapes';
import type { EditorDefinition } from '@/features/editors/types';
import { PAGES_OF, SplitChoice } from '@/features/processing/split';
import { MESSAGES } from '@/shared/messages';

/**
 * The editor of the choice of pages for a scan, for the automatic split: the same line as the split line editor, saved
 * as two pages cut along it.
 *
 * It lies where the line editor does and the edit belongs to the same page, the left one of the scan. Moving the line is a
 * choice of two pages, so the stage is run on the page after every save, also for a scan that was kept whole.
 */

export const splitEditor: EditorDefinition<SplitShape> = {
  picture: lineEditor.picture,
  alwaysOn: lineEditor.alwaysOn,
  needsResult: lineEditor.needsResult,
  owner: lineEditor.owner,
  size: lineEditor.size,
  runsAfterEdit: () => true,
  fallback: ({ size, result }) => ({
    pages: PAGES_OF[SplitChoice.Two],
    line: cutLine(result, size ?? { width: 0, height: 0 }),
  }),
  read: readSplit,
  write: writeSplit,
  // A choice of one page has no line to word, and the cut is found where the gutter is
  describe: ({ line }) =>
    line === null ? MESSAGES.processing.steps.pageHistory.hand.unknown : lineEditor.describe(line),
  Canvas: SplitCanvas,
  Panel: LinePanel,
};
