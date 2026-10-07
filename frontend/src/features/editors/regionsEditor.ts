import { RegionsCanvas } from '@/features/editors/RegionsCanvas';
import { RegionsPanel } from '@/features/editors/RegionsPanel';
import { type RegionsShape, readRegions, writeRegions } from '@/features/editors/shapes';
import { type EditorDefinition, Picture } from '@/features/editors/types';
import { sourceSize } from '@/features/processing/results';
import { MESSAGES } from '@/shared/messages';

/**
 * The editor of the picture zones of a mixed page: the zones the step found, which the reader adds to and removes from.
 *
 * It lies on the picture the binarization reads, which is the page before the stage, and not on the scan. The zones the
 * step found are shown beside the reader's own, and the edit holds only what the reader drew, so a page the reader has not
 * touched is found again by the step.
 */

export const regionsEditor: EditorDefinition<RegionsShape> = {
  picture: Picture.Input,
  alwaysOn: false,
  needsResult: true,
  owner: ({ current }) => current.page,
  size: ({ result }) => sourceSize(result),
  runsAfterEdit: () => true,
  fallback: () => ({ zones: [] }),
  read: readRegions,
  write: writeRegions,
  describe: ({ zones }) => MESSAGES.processing.steps.pageHistory.hand.regions(zones.length),
  Canvas: RegionsCanvas,
  Panel: RegionsPanel,
};
