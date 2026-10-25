import type { QueryClient } from '@tanstack/react-query';
import type { JobSchema, JobState } from '@/api';
import { readJobApiV1BooksBookIdJobsJobIdGetQueryKey } from '@/api/@tanstack/react-query.gen';
import {
  invalidateAllRecipes,
  invalidateAllStageRows,
  invalidateAllVersions,
  invalidateBook,
  invalidateBookList,
  invalidateCollectable,
  invalidateJobs,
  invalidatePages,
  invalidateProfiles,
  invalidateScans,
  invalidateSections,
  invalidateSources,
  invalidateStageSummary,
  type VersionReady,
  versionReadyKey,
} from '@/features/books/queries';
import { numberingPreviewKey } from '@/features/order/usePreviewLabels';
import { invalidateHistory, invalidatePageLayers } from '@/features/processing/historyQueries';
import { Coalescer } from '@/shared/lib/coalescer';

/**
 * What the events of a book do to the cached queries.
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
 * reports a job once per task, so the reads of a part are made at the start and at the end of a burst, not once per
 * event.
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
  'record',
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

/** One event received from the stream. */
export interface BookEvent {
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

/** A read of a book that a change of the book or of its jobs makes stale. */
const BookRead = {
  Book: 'record',
  BookList: 'book-list',
  Sources: 'sources',
  Scans: 'scans',
  Pages: 'pages',
  NumberingPreviews: 'numbering-previews',
  Sections: 'sections',
  StageSummary: 'stage-summary',
  StageRows: 'stage-rows',
  Versions: 'versions',
  Collectable: 'collectable',
  PageLayers: 'page-layers',
  History: 'history',
  Recipes: 'recipes',
  Profiles: 'profiles',
  Jobs: 'jobs',
} as const;

type BookRead = (typeof BookRead)[keyof typeof BookRead];

/** The reads a change of each part of a book makes stale. */
const READS_OF_PART: Record<BookResource, readonly BookRead[]> = {
  record: [BookRead.Book, BookRead.BookList],
  sources: [BookRead.Sources, BookRead.Book, BookRead.BookList],
  // A page whose scan was just cut gets its images in the manifest
  scans: [BookRead.Scans, BookRead.Pages],
  // A section follows its first page, and pages added, removed or moved change the counts and rows of every stage and
  // the labels a numbering would write
  pages: [
    BookRead.Pages,
    BookRead.NumberingPreviews,
    BookRead.Sections,
    BookRead.StageSummary,
    BookRead.StageRows,
    BookRead.Book,
    BookRead.BookList,
  ],
  // The rows of the stages draw the versions, the manifest shows the image of the latest one, and a collection counts
  // the old ones
  versions: [BookRead.Versions, BookRead.Collectable, BookRead.Pages, BookRead.StageRows],
  // The status of each stage and the next stage are part of the book, and its card in the library
  stages: [BookRead.StageSummary, BookRead.StageRows, BookRead.Book, BookRead.BookList],
  settings: [BookRead.PageLayers],
  history: [BookRead.History],
  // The recipe profiles of the book are kept with its recipes
  recipes: [BookRead.Recipes, BookRead.Profiles],
};

/** The reads that show the jobs of a book, which change with every job and raise no version of the book. */
const JOB_READS: readonly BookRead[] = [
  BookRead.Jobs,
  BookRead.StageSummary,
  BookRead.StageRows,
  BookRead.Book,
  BookRead.BookList,
];

function readAgain(queryClient: QueryClient, bookId: string, read: BookRead): Promise<void> {
  switch (read) {
    case BookRead.Book:
      return invalidateBook(queryClient, bookId);
    case BookRead.BookList:
      return invalidateBookList(queryClient);
    case BookRead.Sources:
      return invalidateSources(queryClient, bookId);
    case BookRead.Scans:
      return invalidateScans(queryClient, bookId);
    case BookRead.Pages:
      return invalidatePages(queryClient, bookId);
    case BookRead.NumberingPreviews:
      return queryClient.invalidateQueries({ queryKey: numberingPreviewKey(bookId) });
    case BookRead.Sections:
      return invalidateSections(queryClient, bookId);
    case BookRead.StageSummary:
      return invalidateStageSummary(queryClient, bookId);
    case BookRead.StageRows:
      return invalidateAllStageRows(queryClient, bookId);
    case BookRead.Versions:
      return invalidateAllVersions(queryClient, bookId);
    case BookRead.Collectable:
      return invalidateCollectable(queryClient, bookId);
    case BookRead.PageLayers:
      return invalidatePageLayers(queryClient);
    case BookRead.History:
      return invalidateHistory(queryClient);
    case BookRead.Recipes:
      return invalidateAllRecipes(queryClient, bookId);
    case BookRead.Profiles:
      return invalidateProfiles(queryClient, bookId);
    case BookRead.Jobs:
      return invalidateJobs(queryClient, bookId);
  }
}

/**
 * Read again each of some reads of a book once.
 *
 * Several parts of a book name the same read, and a read asked for twice in one turn costs two requests, since the
 * second invalidation cancels the request of the first after it reached the server.
 */
async function refreshReads(
  queryClient: QueryClient,
  bookId: string,
  reads: Iterable<BookRead>,
): Promise<void> {
  const wanted = new Set(reads);
  // The read of the layers of the pages covers their histories
  if (wanted.has(BookRead.PageLayers)) {
    wanted.delete(BookRead.History);
  }
  await Promise.all([...wanted].map((read) => readAgain(queryClient, bookId, read)));
}

/** Read again the reads that show the jobs of a book. */
export function refreshJobs(queryClient: QueryClient, bookId: string): Promise<void> {
  return refreshReads(queryClient, bookId, JOB_READS);
}

/** Read the whole book again, after a break of the stream in which it changed or after a lost event. */
function refreshBook(queryClient: QueryClient, bookId: string): Promise<void> {
  return refreshReads(queryClient, bookId, [
    ...BOOK_RESOURCES.flatMap((resource) => READS_OF_PART[resource]),
    ...JOB_READS,
  ]);
}

/**
 * The reads the events of the books of one cache ask for, folded into one burst per book.
 *
 * A step of a run commits one change of a book that names several parts, and the parts share reads, such as the rows of
 * the stages, so the reads of a burst are gathered across its events and parts and made once each.
 */
class ReadBursts {
  private readonly queryClient: QueryClient;
  private readonly coalescer = new Coalescer(BURST_DELAY_MS);
  private readonly pending = new Map<string, Set<BookRead>>();

  constructor(queryClient: QueryClient) {
    this.queryClient = queryClient;
  }

  /** Ask for some reads of a book, made at once or at the end of the burst a moment ago began. */
  add(bookId: string, reads: Iterable<BookRead>): void {
    const held = this.pending.get(bookId) ?? new Set<BookRead>();
    for (const read of reads) {
      held.add(read);
    }
    this.pending.set(bookId, held);
    this.coalescer.schedule(bookId, () => {
      const due = this.pending.get(bookId);
      this.pending.delete(bookId);
      if (due !== undefined) {
        void refreshReads(this.queryClient, bookId, due);
      }
    });
  }
}

// The bursts of each cache are folded apart, since a burst is the reads of that cache a run of events asks for
const burstsOfCache = new WeakMap<QueryClient, ReadBursts>();

function burstsOf(queryClient: QueryClient): ReadBursts {
  let bursts = burstsOfCache.get(queryClient);
  if (bursts === undefined) {
    bursts = new ReadBursts(queryClient);
    burstsOfCache.set(queryClient, bursts);
  }
  return bursts;
}

/** Patch or read again the queries an event makes stale, and keep the version of the book the tab holds. */
export function applyBookEvent(
  queryClient: QueryClient,
  bookId: string,
  event: BookEvent,
  sync: BookSync,
): void {
  switch (event.event) {
    case EventName.JobChanged:
      if (isJob(event.data)) {
        queryClient.setQueryData(
          readJobApiV1BooksBookIdJobsJobIdGetQueryKey({
            path: { book_id: bookId, job_id: event.data.id },
          }),
          event.data,
        );
        burstsOf(queryClient).add(bookId, isActiveJob(event.data) ? [BookRead.Jobs] : JOB_READS);
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
        queryClient.setQueryData(versionReadyKey(bookId, event.data.page_id), ready);
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
        void refreshBook(queryClient, bookId);
        break;
      }
      burstsOf(queryClient).add(
        bookId,
        change.resources.flatMap((resource) => READS_OF_PART[resource]),
      );
      break;
    }
    case EventName.BookReset:
      sync.version = versionOf(event.data) ?? sync.version;
      void refreshBook(queryClient, bookId);
      break;
    default:
      // Any event a later server adds changes nothing this interface shows
      break;
  }
}
