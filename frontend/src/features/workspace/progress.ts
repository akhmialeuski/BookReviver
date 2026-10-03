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
  /** Pages that ask for a look: out of date, failed, or marked by the step as unsure, each counted once. */
  check: number;
  /** Pages the stage failed on. */
  failed: number;
  /** Pages run through some of the steps of their recipe only, which the next stage does not read yet. */
  stopped: number;
  /** The coloured parts, in the order they are drawn; none for a stage without pages. */
  segments: ProgressSegment[];
}

const FULL = 100;

/**
 * Work out the numbers of a stage for the bar.
 *
 * The pages to check come from the server, which counts each page that is out of date, failed or marked once. That is
 * the number of pages the Check filter of the strip lists in the same stage.
 *
 * @param summary The summary of the stage.
 */
export function stageProgress(summary: StageSummarySchema): StageProgress {
  const { pages, fresh, stale, failed, not_run: notRun, check, partial } = summary;
  const parts: ProgressSegment[] = [
    { status: 'fresh', percent: pages === 0 ? 0 : (fresh / pages) * FULL },
    { status: 'stale', percent: pages === 0 ? 0 : (stale / pages) * FULL },
    { status: 'failed', percent: pages === 0 ? 0 : (failed / pages) * FULL },
    { status: 'not-run', percent: pages === 0 ? 0 : (notRun / pages) * FULL },
  ];
  return {
    done: fresh,
    total: pages,
    check,
    failed,
    stopped: partial,
    segments: parts.filter((part) => part.percent > 0),
  };
}
