import type { ProjectSchema, Stage, StageStatus } from '@/api';
import { STAGES } from '@/features/stages/stages';

/**
 * The progress of a book over the ten stages as the library draws it, one segment per stage.
 *
 * The list of books carries a status for every stage and the stage the book goes to next. The segments are put in
 * pipeline order here, whatever order the server listed them in, so a card always draws ten of them.
 */

/** One stage of a book and where it stands. */
export interface StageSegment {
  readonly stage: Stage;
  readonly status: StageStatus;
}

/**
 * Give the ten segments of a book in pipeline order.
 *
 * @param progress The status of each stage, as the book carries it.
 * @returns One segment per stage, where a stage the book has no entry for counts as not available.
 */
export function progressSegments(progress: ProjectSchema['progress']): StageSegment[] {
  const statuses = new Map(progress.map((entry) => [entry.stage, entry.status]));
  return STAGES.map(({ stage }) => ({ stage, status: statuses.get(stage) ?? 'unavailable' }));
}

/**
 * Give the stage a book goes to next with its status, which is the reason it is next.
 *
 * @param project The book.
 * @returns The next stage, or null when everything that can be done is done.
 */
export function nextStageOf(
  project: Pick<ProjectSchema, 'progress' | 'next_stage'>,
): StageSegment | null {
  const { next_stage: stage } = project;
  if (stage === null) {
    return null;
  }
  const segment = progressSegments(project.progress).find((entry) => entry.stage === stage);
  return segment ?? { stage, status: 'unavailable' };
}
