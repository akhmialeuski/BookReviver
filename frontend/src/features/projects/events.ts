import type { QueryClient } from '@tanstack/react-query';
import type { JobSchema, JobState } from '@/api';
import { readJobApiV1JobsJobIdGetQueryKey } from '@/api/@tanstack/react-query.gen';
import { numberingPreviewKey } from '@/features/order/usePreviewLabels';
import { invalidateHistory, invalidatePageLayers } from '@/features/processing/historyQueries';
import {
  invalidateAllRecipes,
  invalidateAllStageRows,
  invalidateAllVersions,
  invalidateCollectable,
  invalidateJobs,
  invalidatePages,
  invalidateProject,
  invalidateProjectList,
  invalidateScans,
  invalidateSections,
  invalidateSources,
  invalidateStageSummary,
  type VersionReady,
  versionReadyKey,
} from '@/features/projects/queries';
import { Coalescer } from '@/shared/lib/coalescer';

/**
 * What the events of a project do to the cached queries.
 *
 * Every committed change of a book is one `book-changed` event, which carries the new version of the book and the
 * parts of it the change wrote, so the queries of those parts are read again and nothing else is. The stream opens
 * with `book-version`, which tells the tab the version it holds, and a tab that comes back after a break is sent
 * `book-reset` when the book changed meanwhile, since the stream keeps no changes. A version that is not one more than
 * the last one seen means an event was lost, so the whole book is read again.
 *
 * The jobs live in the memory of the server and raise no version of the book, so a job event carries the whole job, which
 * is written straight into the query of that job, and the reads that show jobs are read again when a job ends. A version
 * a page gains is announced with its identifier, which a preview waits for. A run changes the book once per page and
 * reports a job once per task, so the reads of a part are made once per burst, not once per event.
 *
 * A change made on this screen reads again only what the control that made it shows, so that control never shows the
 * state before it for the length of a burst. Everything else the change wrote follows its `book-changed` event. A change
 * that submits a job reads the jobs again too, since they raise no version of the book.
 */

/** Names of the server-sent events, as `EventName` of the backend spells them. */
export const EventName = {
  JobChanged: 'job-changed',
  PageVersionReady: 'page-version-ready',
  BookVersion: 'book-version',
  BookChanged: 'book-changed',
  BookReset: 'book-reset',
} as const;

/** The parts of a book a change names, as `BookResource` of the backend spells them. */
export const BOOK_RESOURCES = [
  'project',
  'sources',
  'scans',
  'pages',
  'versions',
  'stages',
  'settings',
  'history',
  'recipes',
] as const;

export type BookResource = (typeof BOOK_RESOURCES)[number];

/** How long the first event of a burst waits for the rest before the queries it affects are read again. */
export const BURST_DELAY_MS = 400;

const bursts = new Coalescer(BURST_DELAY_MS);

/** One event received from the stream. */
export interface ProjectEvent {
  event?: string;
  data?: unknown;
}

/** What one tab knows of the book it listens to: the last version the stream told it, or null before the first. */
export interface BookSync {
  version: number | null;
}

/** A committed change of a book, as the data of a `book-changed` event. */
interface BookChange {
  version: number;
  resources: BookResource[];
}

const JOB_STATES: ReadonlySet<string> = new Set<JobState>([
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled',
]);

const ACTIVE_JOB_STATES: ReadonlySet<string> = new Set<JobState>(['queued', 'running']);

const KNOWN_RESOURCES: ReadonlySet<string> = new Set<string>(BOOK_RESOURCES);

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

/** Read the version an event of the book names, or null for data that names none. */
function versionOf(data: unknown): number | null {
  return isRecord(data) && typeof data.version === 'number' ? data.version : null;
}

/** Read a change of a book out of the data of a `book-changed` event, or null for data that is not one. */
function bookChangeOf(data: unknown): BookChange | null {
  const version = versionOf(data);
  if (version === null || !isRecord(data) || !Array.isArray(data.resources)) {
    return null;
  }
  const resources = data.resources.filter(
    (resource): resource is BookResource =>
      typeof resource === 'string' && KNOWN_RESOURCES.has(resource),
  );
  return { version, resources };
}

/** Read again the reads that show the jobs of a book, which change with every job and raise no version of the book. */
export async function refreshJobs(queryClient: QueryClient, projectId: string): Promise<void> {
  await Promise.all([
    invalidateJobs(queryClient, projectId),
    invalidateStageSummary(queryClient, projectId),
    invalidateAllStageRows(queryClient, projectId),
    invalidateProject(queryClient, projectId),
    invalidateProjectList(queryClient),
  ]);
}

/** Read again the queries of one part of a book a change wrote. */
export async function refreshPart(
  queryClient: QueryClient,
  projectId: string,
  resource: BookResource,
): Promise<void> {
  switch (resource) {
    case 'project':
      await Promise.all([
        invalidateProject(queryClient, projectId),
        invalidateProjectList(queryClient),
      ]);
      return;
    case 'sources':
      await Promise.all([
        invalidateSources(queryClient, projectId),
        invalidateProject(queryClient, projectId),
        invalidateProjectList(queryClient),
      ]);
      return;
    case 'scans':
      // A page whose scan was just cut gets its images in the manifest
      await Promise.all([
        invalidateScans(queryClient, projectId),
        invalidatePages(queryClient, projectId),
      ]);
      return;
    case 'pages':
      // A section follows its first page, and pages added, removed or moved change the counts and rows of every stage
      // and the labels a numbering would write
      await Promise.all([
        invalidatePages(queryClient, projectId),
        queryClient.invalidateQueries({ queryKey: numberingPreviewKey(projectId) }),
        invalidateSections(queryClient, projectId),
        invalidateStageSummary(queryClient, projectId),
        invalidateAllStageRows(queryClient, projectId),
        invalidateProject(queryClient, projectId),
        invalidateProjectList(queryClient),
      ]);
      return;
    case 'versions':
      // The rows of the stages draw the versions, the manifest shows the image of the latest one, and a collection
      // counts the old ones
      await Promise.all([
        invalidateAllVersions(queryClient, projectId),
        invalidateCollectable(queryClient, projectId),
        invalidatePages(queryClient, projectId),
        invalidateAllStageRows(queryClient, projectId),
      ]);
      return;
    case 'stages':
      // The status of each stage and the next stage are part of the book, and its card in the library
      await Promise.all([
        invalidateStageSummary(queryClient, projectId),
        invalidateAllStageRows(queryClient, projectId),
        invalidateProject(queryClient, projectId),
        invalidateProjectList(queryClient),
      ]);
      return;
    case 'settings':
      await invalidatePageLayers(queryClient);
      return;
    case 'history':
      await invalidateHistory(queryClient);
      return;
    case 'recipes':
      await invalidateAllRecipes(queryClient, projectId);
      return;
  }
}

/** Read the whole book again, after a break of the stream in which it changed or after a lost event. */
export async function refreshBook(queryClient: QueryClient, projectId: string): Promise<void> {
  await Promise.all([
    ...BOOK_RESOURCES.map((resource) => refreshPart(queryClient, projectId, resource)),
    refreshJobs(queryClient, projectId),
  ]);
}

/** Patch or read again the queries an event makes stale, and keep the version of the book the tab holds. */
export function applyProjectEvent(
  queryClient: QueryClient,
  projectId: string,
  event: ProjectEvent,
  sync: BookSync,
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
          void refreshJobs(queryClient, projectId);
        }
      }
      break;
    case EventName.PageVersionReady:
      if (
        isRecord(event.data) &&
        typeof event.data.page_id === 'string' &&
        typeof event.data.version_id === 'string'
      ) {
        // A preview waits for this; the results of the page are read again by the change of the book that wrote it
        const ready: VersionReady = { versionId: event.data.version_id, at: Date.now() };
        queryClient.setQueryData(versionReadyKey(projectId, event.data.page_id), ready);
      }
      break;
    case EventName.BookVersion:
      sync.version = versionOf(event.data) ?? sync.version;
      break;
    case EventName.BookChanged: {
      const change = bookChangeOf(event.data);
      if (change === null) {
        break;
      }
      const lost = sync.version !== null && change.version !== sync.version + 1;
      sync.version = change.version;
      if (lost) {
        void refreshBook(queryClient, projectId);
        break;
      }
      for (const resource of change.resources) {
        bursts.schedule(
          `${projectId}/book/${resource}`,
          () => void refreshPart(queryClient, projectId, resource),
        );
      }
      break;
    }
    case EventName.BookReset:
      sync.version = versionOf(event.data) ?? sync.version;
      void refreshBook(queryClient, projectId);
      break;
    default:
      // Any event a later server adds changes nothing this interface shows
      break;
  }
}
