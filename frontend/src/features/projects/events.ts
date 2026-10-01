import type { QueryClient } from '@tanstack/react-query';
import type { JobSchema, JobState } from '@/api';
import { readJobApiV1JobsJobIdGetQueryKey } from '@/api/@tanstack/react-query.gen';
import {
  invalidatePages,
  invalidateProject,
  invalidateProjectList,
  invalidateScans,
  invalidateSources,
} from '@/features/projects/queries';

/**
 * What the events of a project do to the cached queries.
 *
 * The server pushes events when a job changes, a source is imported, a scan becomes ready, pages change or the
 * description changes. A job event carries the whole job, so it is written straight into the query of that job and
 * the progress bar moves without a request. The other events only say that something changed, so they mark the
 * affected queries stale, and TanStack Query refetches the ones a screen is showing.
 */

/** Names of the server-sent events, as `EventName` of the backend spells them. */
export const EventName = {
  JobChanged: 'job-changed',
  SourceImported: 'source-imported',
  ScanReady: 'scan-ready',
  PagesChanged: 'pages-changed',
  PageVersionReady: 'page-version-ready',
  ProjectChanged: 'project-changed',
} as const;

/** One event received from the stream. */
export interface ProjectEvent {
  event?: string;
  data?: unknown;
}

const JOB_STATES: ReadonlySet<string> = new Set<JobState>([
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled',
]);
const ACTIVE_JOB_STATES: ReadonlySet<string> = new Set<JobState>(['queued', 'running']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Tell whether a job is still queued or running, so something will still change in it. */
export function isActiveJob(job: Pick<JobSchema, 'state'> | undefined): boolean {
  return job !== undefined && ACTIVE_JOB_STATES.has(job.state);
}

/** Check the parts of an event's data that the interface reads before trusting it as a job. */
export function isJob(value: unknown): value is JobSchema {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.state === 'string' &&
    JOB_STATES.has(value.state) &&
    isRecord(value.progress) &&
    typeof value.progress.done === 'number' &&
    typeof value.progress.total === 'number' &&
    typeof value.progress.fraction === 'number'
  );
}

/** Refresh everything about a book that an import can change. */
export async function refreshProject(queryClient: QueryClient, projectId: string): Promise<void> {
  await Promise.all([
    invalidateProject(queryClient, projectId),
    invalidateSources(queryClient, projectId),
    invalidateScans(queryClient, projectId),
    invalidateProjectList(queryClient),
    invalidatePages(queryClient, projectId),
  ]);
}

/** Patch or invalidate the queries an event makes stale. */
export function applyProjectEvent(
  queryClient: QueryClient,
  projectId: string,
  event: ProjectEvent,
): void {
  switch (event.event) {
    case EventName.JobChanged:
      if (isJob(event.data)) {
        queryClient.setQueryData(
          readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: event.data.id } }),
          event.data,
        );
        if (!isActiveJob(event.data)) {
          void refreshProject(queryClient, projectId);
        }
      }
      break;
    case EventName.SourceImported:
      void invalidateSources(queryClient, projectId);
      void invalidatePages(queryClient, projectId);
      void invalidateProject(queryClient, projectId);
      void invalidateProjectList(queryClient);
      break;
    case EventName.ScanReady:
      void invalidateScans(queryClient, projectId);
      // A page whose scan was just cut gets its images in the manifest
      void invalidatePages(queryClient, projectId);
      break;
    case EventName.PagesChanged:
      void invalidatePages(queryClient, projectId);
      void invalidateProject(queryClient, projectId);
      void invalidateProjectList(queryClient);
      break;
    case EventName.PageVersionReady:
      // The image of a blank leaf or of a bound scan is written by a job, and its path appears in the manifest
      void invalidatePages(queryClient, projectId);
      break;
    case EventName.ProjectChanged:
      void invalidateProject(queryClient, projectId);
      void invalidateProjectList(queryClient);
      break;
    default:
      // Any event a later server adds changes nothing this interface shows
      break;
  }
}
