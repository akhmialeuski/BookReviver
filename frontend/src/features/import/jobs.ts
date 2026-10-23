import type { JobSchema } from '@/api';
import { isActiveJob } from '@/features/books/events';

/**
 * Which import jobs of a book the Import stage shows: those that run, as rows of the list of files, and the latest
 * one that failed, as the reason in the report under the list.
 *
 * What an import did with its files is not a property of the job: every file is a source with a status, and the
 * rejected and skipped ones are the report, which `useImportReport` reads. The jobs live in the memory of the server,
 * so a job that ended before the server started is not here, and a failure is shown only while the server remembers it.
 */

/** The import jobs of a book that have not ended and the failure of the latest one that has. */
export interface ImportJobs {
  /** Queued or running imports, the oldest first, which is the order their files join the book. */
  active: JobSchema[];
  /** The latest ended import, when it failed and the person did not dismiss it. */
  failed: JobSchema | undefined;
}

function createdAt(job: JobSchema): number {
  return Date.parse(job.created_at);
}

/**
 * Sort the jobs of a book into the imports that run and the failure to show.
 *
 * @param jobs The jobs of the book of any kind and in any order.
 * @param dismissed Identifiers of the jobs whose failure the person has closed.
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
    failed: latest?.state === 'failed' && !dismissed.has(latest.id) ? latest : undefined,
  };
}
