import type { PageStageStatus, StageSummarySchema } from '@/api';

/**
 * What the stage bar draws for a stage, worked out from the summary of the stage over the pages of the book.
 */

/** One coloured part of the progress bar of a stage. */
export interface ProgressSegment {
  status: PageStageStatus;
  /** Share of the pages in this status, in percent. */
  percent: number;
}

/** The numbers of one stage in the bar. */
export interface StageProgress {
  /** Pages whose result is up to date. */
  done: number;
  /** Pages the stage goes over. */
  total: number;
  /** Pages that ask for a look: out of date, or marked by the step as unsure. */
  check: number;
  /** Pages the stage failed on. */
  failed: number;
  /** The coloured parts, in the order they are drawn; none for a stage without pages. */
  segments: ProgressSegment[];
}

const FULL = 100;

/**
 * Work out the numbers of a stage for the bar.
 *
 * The summary counts the pages marked as unsure on their own, and a marked page can also be up to date or out of
 * date, so the pages to check are the out-of-date ones plus the marked ones, at most the pages that did not fail.
 * That is an upper bound, since a page both out of date and marked is counted twice.
 *
 * @param summary The summary of the stage.
 */
export function stageProgress(summary: StageSummarySchema): StageProgress {
  const { pages, fresh, stale, failed, not_run: notRun, review } = summary;
  const parts: ProgressSegment[] = [
    { status: 'fresh', percent: pages === 0 ? 0 : (fresh / pages) * FULL },
    { status: 'stale', percent: pages === 0 ? 0 : (stale / pages) * FULL },
    { status: 'failed', percent: pages === 0 ? 0 : (failed / pages) * FULL },
    { status: 'not-run', percent: pages === 0 ? 0 : (notRun / pages) * FULL },
  ];
  return {
    done: fresh,
    total: pages,
    check: Math.min(stale + review, Math.max(pages - failed, 0)),
    failed,
    segments: parts.filter((part) => part.percent > 0),
  };
}
