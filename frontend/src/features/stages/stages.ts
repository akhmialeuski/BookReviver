import type { PageStageStatus, Stage, StageStatus } from '@/api';

/**
 * The stages of a book as the interface groups and draws them, shared by the stage bar, the strip and the library.
 *
 * The order and the phase of every stage live here once, so a stage bar and a book card never disagree. A state has
 * one colour, the `status-*` token of `index.css`, wherever it is drawn, which is principle 6 of the stage epic. The
 * texts are in `MESSAGES.stages`.
 */

/** The four groups of the stage bar, in the order a book goes through them. */
export const Phase = {
  Prepare: 'prepare',
  Image: 'image',
  Text: 'text',
  Publish: 'publish',
} as const;

/** One phase (derived from {@link Phase}). */
export type Phase = (typeof Phase)[keyof typeof Phase];

/** A stage of the pipeline with the phase it belongs to. */
export interface StageEntry {
  readonly stage: Stage;
  readonly phase: Phase;
}

/** Every stage in pipeline order, the order the API lists them in. */
export const STAGES: readonly StageEntry[] = [
  { stage: 'import', phase: Phase.Prepare },
  { stage: 'page-split', phase: Phase.Prepare },
  { stage: 'page-order', phase: Phase.Prepare },
  { stage: 'geometry', phase: Phase.Image },
  { stage: 'cleanup', phase: Phase.Image },
  { stage: 'layout', phase: Phase.Image },
  { stage: 'background', phase: Phase.Image },
  { stage: 'recognition', phase: Phase.Text },
  { stage: 'proofreading', phase: Phase.Text },
  { stage: 'typesetting', phase: Phase.Publish },
];

/** The background class of the colour of each status of a whole stage. */
export const STAGE_STATUS_TONE: Record<StageStatus, string> = {
  done: 'bg-status-done',
  attention: 'bg-status-attention',
  running: 'bg-status-running',
  waiting: 'bg-status-idle',
  unavailable: 'bg-muted',
};

/** The background class of the colour of each status of one page in one stage. */
export const PAGE_STATUS_TONE: Record<PageStageStatus, string> = {
  fresh: 'bg-status-done',
  stale: 'bg-status-attention',
  failed: 'bg-status-failed',
  'not-run': 'bg-status-idle',
};

/** The class marking a page whose result asks for a second look, which is not a state of the stage on its own. */
export const REVIEW_TONE = 'bg-status-attention';

/**
 * Give the number of a stage as the stage bar shows it, from one.
 *
 * @param stage The stage.
 * @returns Its place in the pipeline, counted from one.
 */
export function stageNumber(stage: Stage): number {
  return STAGES.findIndex((entry) => entry.stage === stage) + 1;
}

/**
 * Give the stage that comes right before a stage in the pipeline.
 *
 * @param stage The stage.
 * @returns The stage before it, or null for the first.
 */
export function stageBefore(stage: Stage): Stage | null {
  const index = STAGES.findIndex((entry) => entry.stage === stage);
  return STAGES[index - 1]?.stage ?? null;
}
