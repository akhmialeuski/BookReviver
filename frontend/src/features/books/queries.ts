import type { QueryClient, QueryKey } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  bookApiV1BooksBookIdGetQueryKey,
  collectableVersionsApiV1BooksBookIdVersionsCollectableGetQueryKey,
  listBookJobsApiV1BooksBookIdJobsGetQueryKey,
  listBooksApiV1BooksGetQueryKey,
  listPagesApiV1BooksBookIdPagesGetQueryKey,
  listPaginationSectionsApiV1BooksBookIdPaginationSectionsGetQueryKey,
  listRecipesApiV1BooksBookIdStagesStageRecipesGetQueryKey,
  listScansApiV1BooksBookIdScansGetQueryKey,
  listSourcesApiV1BooksBookIdSourcesGetQueryKey,
  listStagePagesApiV1BooksBookIdStagesStagePagesGetQueryKey,
  listStagesApiV1BooksBookIdStagesGetQueryKey,
  listVersionsApiV1BooksBookIdPagesPageIdVersionsGetQueryKey,
  readJobApiV1JobsJobIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { STAGES } from '@/features/stages/stages';

// A stage to build the key of the recipes of a book with, whose identifier and address alone are kept
const ANY_STAGE: Stage = 'import';

/**
 * Which cached queries a change of a book makes stale.
 *
 * The keys come from the generated client, and a key without parameters matches every query of its endpoint, so
 * one call refreshes all pages and filters of a list. The event stream calls these for the parts of the book a change
 * named, and the changes of the pages call them as they end.
 */

/**
 * Mark the queries of a key stale and read them again after a change the screen has just made.
 *
 * `invalidateQueries` on a query whose first read is still in flight joins that read instead of starting a new one, so
 * the answer it brings was asked for before the change and lacks it. The reads in flight are cancelled first, which
 * leaves the query without a read, and the invalidation then starts a read that comes after the change.
 *
 * @param queryClient The client that holds the queries.
 * @param queryKey The key of the queries to refresh; a key that is a prefix matches every query that extends it.
 */
export async function refreshQueries(queryClient: QueryClient, queryKey: QueryKey): Promise<void> {
  await queryClient.cancelQueries({ queryKey });
  await queryClient.invalidateQueries({ queryKey });
}

/** Refresh the list of books, whose counts and order change when a book is created, deleted or imported into. */
export function invalidateBookList(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: listBooksApiV1BooksGetQueryKey() });
}

/** Refresh the book itself with its counts. */
export function invalidateBook(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: bookApiV1BooksBookIdGetQueryKey({ path: { book_id: bookId } }),
  });
}

/** Refresh the sources of a book. */
export function invalidateSources(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listSourcesApiV1BooksBookIdSourcesGetQueryKey({
      path: { book_id: bookId },
    }),
  });
}

/**
 * The mutation scope of the changes that rewrite pages that exist, which TanStack Query runs one after the other.
 *
 * The server reads a page, changes it and writes the whole row, so two changes of one page that are in flight together
 * each write back the page as it was before the other, and the first change is lost.
 */
export function pagesScope(bookId: string): { id: string } {
  return { id: `pages-${bookId}` };
}

/**
 * Count the changes that rewrite pages of a book and are queued or in flight.
 *
 * While there are some, the pages read from the server lack a change that the screen already shows, so a read must
 * wait, and the last of the changes reads the pages when it ends.
 */
export function pageChangesInFlight(queryClient: QueryClient, bookId: string): number {
  const { id } = pagesScope(bookId);
  return queryClient.isMutating({ predicate: (mutation) => mutation.options.scope?.id === id });
}

/**
 * Refresh the pages of a book, in every request shape the manifest has been read with, unless a change of the pages is
 * still queued or in flight.
 *
 * A read made while a change is in flight lacks it and writes the pages without it over what the screen shows, so the
 * grid flips a moved page back until the next read. Every read of the pages comes through here, the ones the events of
 * the book ask for included, and the last change to end reads the pages and sees whatever the skipped reads would have.
 *
 * @param queryClient The client that holds the queries.
 * @param projectId The book whose pages to refresh.
 * @param settling How many of the changes in flight are the one that calls this, one for a change of the pages that
 * reads them as it ends and none for anything else.
 */
export async function invalidatePages(
  queryClient: QueryClient,
  bookId: string,
  settling = 0,
): Promise<void> {
  if (pageChangesInFlight(queryClient, bookId) > settling) {
    return;
  }
  await queryClient.invalidateQueries({
    queryKey: listPagesApiV1BooksBookIdPagesGetQueryKey({ path: { book_id: bookId } }),
  });
}

/** Refresh the pagination sections of a book, which a change of its pages can move, hand over or renumber. */
export function invalidateSections(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listPaginationSectionsApiV1BooksBookIdPaginationSectionsGetQueryKey({
      path: { book_id: bookId },
    }),
  });
}

/** Refresh the summary of the stages of a book, which the stage bar draws. */
export function invalidateStageSummary(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listStagesApiV1BooksBookIdStagesGetQueryKey({
      path: { book_id: bookId },
    }),
  });
}

/** Refresh the rows of one stage, where each page of the book stands in it, which the strip draws. */
export function invalidateStageRows(
  queryClient: QueryClient,
  bookId: string,
  stage: Stage,
): Promise<void> {
  return refreshQueries(
    queryClient,
    listStagePagesApiV1BooksBookIdStagesStagePagesGetQueryKey({
      path: { book_id: bookId, stage },
    }),
  );
}

/** Refresh the rows of every stage; only the stage on screen is read again at once, the others when it is opened. */
export async function invalidateAllStageRows(
  queryClient: QueryClient,
  bookId: string,
): Promise<void> {
  await Promise.all(STAGES.map(({ stage }) => invalidateStageRows(queryClient, bookId, stage)));
}

/** What the event of a ready version leaves in the cache for the page it is about. */
export interface VersionReady {
  versionId: string;
  /** When the event arrived, so a version announced twice is still a change the screen waiting for it sees. */
  at: number;
}

/** The key under which the last version announced ready for a page is kept, which a preview waits on. */
export function versionReadyKey(bookId: string, pageId: string): readonly string[] {
  return ['version-ready', bookId, pageId];
}

/** Refresh the results of a page, which a run adds to when its version is ready. */
export function invalidateVersions(
  queryClient: QueryClient,
  bookId: string,
  pageId: string,
): Promise<void> {
  return refreshQueries(
    queryClient,
    listVersionsApiV1BooksBookIdPagesPageIdVersionsGetQueryKey({
      path: { book_id: bookId, page_id: pageId },
    }),
  );
}

/**
 * Refresh the results of every page of a book, which a collection of old versions changes by taking the files of some
 * away. The key names the book alone, so it matches the results of each of its pages.
 */
export function invalidateAllVersions(queryClient: QueryClient, bookId: string): Promise<void> {
  const [{ _id, baseUrl }] = listVersionsApiV1BooksBookIdPagesPageIdVersionsGetQueryKey({
    path: { book_id: bookId, page_id: '' },
  });
  return queryClient.invalidateQueries({
    queryKey: [{ _id, baseUrl, path: { book_id: bookId } }],
  });
}

/** Refresh the count of what a collection of old versions of a book would delete, which every new or marked result changes. */
export function invalidateCollectable(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: collectableVersionsApiV1BooksBookIdVersionsCollectableGetQueryKey({
      path: { book_id: bookId },
    }),
  });
}

/** Refresh the recipes of every stage of a book. The key names the book alone, so it matches the recipes of each stage. */
export function invalidateAllRecipes(queryClient: QueryClient, bookId: string): Promise<void> {
  const [{ _id, baseUrl }] = listRecipesApiV1BooksBookIdStagesStageRecipesGetQueryKey({
    path: { book_id: bookId, stage: ANY_STAGE },
  });
  return queryClient.invalidateQueries({
    queryKey: [{ _id, baseUrl, path: { book_id: bookId } }],
  });
}

/** Refresh every job read one by one, such as the job a preview waits for, whose events a break of the stream lost. */
export function invalidateJobReads(queryClient: QueryClient): Promise<void> {
  const [{ _id, baseUrl }] = readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: '' } });
  return queryClient.invalidateQueries({ queryKey: [{ _id, baseUrl }] });
}

/** Refresh the jobs of a book, those running and the latest of any state, which the activity shows. */
export function invalidateJobs(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listBookJobsApiV1BooksBookIdJobsGetQueryKey({
      path: { book_id: bookId },
    }),
  });
}

/** Refresh the scans of a book, in every filter. */
export function invalidateScans(queryClient: QueryClient, bookId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listScansApiV1BooksBookIdScansGetQueryKey({ path: { book_id: bookId } }),
  });
}
