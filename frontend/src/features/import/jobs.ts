import type { JobSchema } from '@/api';
import { isActiveJob } from '@/features/projects/events';

/**
 * Which import jobs of a book the Import stage shows: those that run, as rows of the list of files, and the latest
 * one that ended with something to tell, as the report under the list.
 */

/** The import jobs of a book that have not ended and the report of the latest one that has. */
export interface ImportJobs {
  /** Queued or running imports, the oldest first, which is the order their files join the book. */
  active: JobSchema[];
  /** The latest ended import that failed, rejected or skipped files, unless the person dismissed it. */
  report: JobSchema | undefined;
}

function createdAt(job: JobSchema): number {
  return Date.parse(job.created_at);
}

function hasReport(job: JobSchema): boolean {
  return (
    job.state === 'failed' ||
    (job.result?.rejected.length ?? 0) > 0 ||
    (job.result?.skipped.length ?? 0) > 0
  );
}

/**
 * Sort the jobs of a book into the imports that run and the report to show.
 *
 * @param jobs The jobs of the book of any kind and in any order.
 * @param dismissed Identifiers of the jobs whose report the person has closed.
 */
export function importJobsOf(
  jobs: readonly JobSchema[],
  dismissed: ReadonlySet<string>,
): ImportJobs {
  const imports = jobs.filter((job) => job.kind === 'import-source');
  const ended = imports
    .filter((job) => !isActiveJob(job))
    .sort((first, second) => createdAt(second) - createdAt(first));
  const latest = ended[0];
  return {
    active: imports
      .filter(isActiveJob)
      .sort((first, second) => createdAt(first) - createdAt(second)),
    report:
      latest !== undefined && hasReport(latest) && !dismissed.has(latest.id) ? latest : undefined,
  };
}
