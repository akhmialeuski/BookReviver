import { readResult } from '@/features/processing/results';
import { needsCheck, type StripItem } from '@/features/workspace/strip';
import { MESSAGES } from '@/shared/messages';

/**
 * The words under a page of the strip that asks for a look, which say why: it failed, it is out of date, or the step
 * finished and was not sure.
 *
 * A page that asks for nothing has no reason, and the strip draws nothing under it.
 *
 * @param item A page of the strip with its row in the stage.
 * @returns The reason, or null for a page that needs no look.
 */
export function reasonOf(item: StripItem): string | null {
  const { row } = item;
  if (row === undefined || !needsCheck(item)) {
    return null;
  }
  const words = MESSAGES.processing.reasons;
  if (row.status === 'failed') {
    return words.failed(row.version?.error ?? '');
  }
  if (row.status === 'stale') {
    return words.stale;
  }
  const confidence = row.version === null ? null : readResult(row.version).confidence;
  switch (row.review) {
    case 'not-applied':
      return words.notApplied(confidence);
    case 'unsure-gutter':
      return words.unsureGutter(confidence);
    case 'narrow-gutter':
      return words.narrowGutter;
    default:
      return words.lowConfidence(confidence);
  }
}
