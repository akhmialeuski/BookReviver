import type { JobSchema, JobState } from '@/api';

/**
 * Picking and describing the jobs of a book for the activity chip and its list.
 *
 * The wording is in `MESSAGES.activity`; this module decides which job the chip shows and which facts a row gives.
 */

const ACTIVE_STATES: ReadonlySet<JobState> = new Set<JobState>(['queued', 'running']);
const TIME_FORMAT = new Intl.DateTimeFormat('en', { timeStyle: 'short' });

/** Tell whether a job is queued or running, which is when it can still be stopped. */
export function isStoppable(job: Pick<JobSchema, 'state'>): boolean {
  return ACTIVE_STATES.has(job.state);
}

function startedAt(job: JobSchema): number {
  return Date.parse(job.started_at ?? job.created_at);
}

/**
 * Pick the job the chip shows: the most recently started running job, or the most recent queued one when none runs.
 *
 * @param jobs The jobs of the book, in any order; finished jobs are ignored.
 * @returns The job, or undefined when nothing is queued or running.
 */
export function latestActiveJob(jobs: readonly JobSchema[]): JobSchema | undefined {
  const active = jobs.filter(isStoppable);
  const byRecency = (first: JobSchema, second: JobSchema): number =>
    startedAt(second) - startedAt(first);
  const running = active.filter((job) => job.state === 'running').sort(byRecency);
  return running[0] ?? active.sort(byRecency)[0];
}

/**
 * Write the time of a timestamp the way a row of the list gives it, such as `10:42 AM`.
 *
 * @returns The time, or an empty text for a timestamp that is not a date.
 */
export function formatTime(timestamp: string | null): string {
  const date = new Date(timestamp ?? '');
  return Number.isNaN(date.getTime()) ? '' : TIME_FORMAT.format(date);
}

/** The moment a row of the list dates a job by: when it ended, else when it started, else when it was recorded. */
export function jobMoment(job: JobSchema): string {
  return job.finished_at ?? job.started_at ?? job.created_at;
}
