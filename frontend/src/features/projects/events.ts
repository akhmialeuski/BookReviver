import type { QueryClient } from '@tanstack/react-query';
import type { JobKind, JobSchema, JobState, Stage } from '@/api';
import { readJobApiV1JobsJobIdGetQueryKey } from '@/api/@tanstack/react-query.gen';
import {
  invalidateAllStageRows,
  invalidateAllVersions,
  invalidateJobs,
  invalidatePages,
  invalidateProject,
  invalidateProjectList,
  invalidateRecipes,
  invalidateScans,
  invalidateSections,
  invalidateSources,
  invalidateStageRowsFrom,
  invalidateStageSummary,
  invalidateVersions,
  pageChangesInFlight,
  type VersionReady,
  versionReadyKey,
} from '@/features/projects/queries';
import { parseStage } from '@/features/stages/parse';
import { Coalescer } from '@/shared/lib/coalescer';

/**
 * What the events of a project do to the cached queries.
 *
 * The server pushes events when a job changes, a source is imported, a scan becomes ready, pages change, a page
 * changes in a stage or the description changes. A job event carries the whole job, so it is written straight into the
 * query of that job. The other events only say that something changed, so they mark the affected queries stale, and
 * TanStack Query refetches the ones a screen is showing. A run sends an event per page and per job step, so the
 * queries of the stage bar, the strip and the activity are marked stale once per burst, not once per event.
 */

/** Names of the server-sent events, as `EventName` of the backend spells them. */
export const EventName = {
  JobChanged: 'job-changed',
  SourceImported: 'source-imported',
  ScanReady: 'scan-ready',
  PagesChanged: 'pages-changed',
  PageVersionReady: 'page-version-ready',
  PageStageChanged: 'page-stage-changed',
  ProjectChanged: 'project-changed',
} as const;

/** How long the first event of a burst waits for the rest before the queries it affects are read again. */
export const BURST_DELAY_MS = 400;

const bursts = new Coalescer(BURST_DELAY_MS);

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
/** The kind of the job that measures the book, and the stage whose recipe it writes into. */
const MEASURE_BOOK_KIND: JobKind = 'measure-book';
const GEOMETRY_STAGE: Stage = 'geometry';
/** The kind of the job that takes the files of the old results of the pages away. */
const COLLECT_VERSIONS_KIND: JobKind = 'collect-versions';

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
    invalidateSections(queryClient, projectId),
    invalidateStageSummary(queryClient, projectId),
    invalidateAllStageRows(queryClient, projectId),
    invalidateJobs(queryClient, projectId),
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
        if (isActiveJob(event.data)) {
          bursts.schedule(`${projectId}/jobs`, () => void invalidateJobs(queryClient, projectId));
        } else {
          void refreshProject(queryClient, projectId);
          if (event.data.kind === MEASURE_BOOK_KIND) {
            // The job wrote the line height and the page size into the recipe of the Geometry stage
            void invalidateRecipes(queryClient, projectId, GEOMETRY_STAGE);
          }
          if (event.data.kind === COLLECT_VERSIONS_KIND) {
            // The job deleted the old results with their files, so the lists of results must not offer them any more
            void invalidateAllVersions(queryClient, projectId);
          }
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
      // A change of the reader's own is still queued or in flight, so the pages read now would lack it and flip the
      // page order on screen back. That change reads the pages again when it ends, and sees this event's change too
      if (pageChangesInFlight(queryClient, projectId) === 0) {
        void invalidatePages(queryClient, projectId);
      }
      // A section follows its first page, so it moves, is handed on or is renumbered with the pages
      void invalidateSections(queryClient, projectId);
      void invalidateProject(queryClient, projectId);
      void invalidateProjectList(queryClient);
      // Pages added, removed or moved change the counts of every stage and the order of its rows
      void invalidateStageSummary(queryClient, projectId);
      void invalidateAllStageRows(queryClient, projectId);
      break;
    case EventName.PageStageChanged: {
      const stage = isRecord(event.data) ? parseStage(event.data.stage) : null;
      if (stage !== null) {
        bursts.schedule(`${projectId}/stage/${stage}`, () => {
          void invalidateStageSummary(queryClient, projectId);
          // The rows of the later stages draw the current version of this one
          void invalidateStageRowsFrom(queryClient, projectId, stage);
          // The status of each stage and the next stage are part of the book, and its card in the library
          void invalidateProject(queryClient, projectId);
          void invalidateProjectList(queryClient);
        });
      }
      break;
    }
    case EventName.PageVersionReady:
      // The image of a blank leaf or of a bound scan is written by a job, and its path appears in the manifest
      void invalidatePages(queryClient, projectId);
      if (
        isRecord(event.data) &&
        typeof event.data.page_id === 'string' &&
        typeof event.data.version_id === 'string'
      ) {
        // A preview waits for this, and the history of the page gains the result of a full run
        const ready: VersionReady = { versionId: event.data.version_id, at: Date.now() };
        queryClient.setQueryData(versionReadyKey(projectId, event.data.page_id), ready);
        void invalidateVersions(queryClient, projectId, event.data.page_id);
      }
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
