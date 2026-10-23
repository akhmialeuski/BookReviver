import { queryOptions, type UseQueryResult, useMutation, useQuery } from '@tanstack/react-query';
import {
  type JobSchema,
  listSourcesApiV1BooksBookIdSourcesGet,
  type SourceSchema,
  type SourceStatus,
} from '@/api';
import {
  deleteUnimportedSourcesApiV1BooksBookIdSourcesUnimportedDeleteMutation,
  listScansApiV1BooksBookIdScansGetOptions,
  listSourcesApiV1BooksBookIdSourcesGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { jobsOptions } from '@/features/workspace/queries';

/**
 * The queries behind the Import stage: every file of the book as one list, the report of the files an import did not
 * take in, and the jobs that import files into it.
 *
 * All are marked stale by the event stream through the generated keys, as the manifest of pages is.
 */

/** A file that joined the book, which always has its kind and its type, unlike a file of the report. */
export type ImportedSource = SourceSchema & {
  kind: NonNullable<SourceSchema['kind']>;
  file_type: NonNullable<SourceSchema['file_type']>;
};

/** The statuses of the files the report of an import lists: those a check rejected and those a cancel never reached. */
const REPORTED_STATUSES: SourceStatus[] = ['rejected', 'skipped'];

/** Narrow a file the server listed as imported, which the list of the sources holds alone, to one with its type. */
function isImported(source: SourceSchema): source is ImportedSource {
  return source.kind !== null && source.file_type !== null;
}

/** Files asked for per request; the route accepts at most this many. */
const SOURCES_PAGE_SIZE = 100;

/** Scans asked for per page of the grid; the route accepts at most 100. */
const SCANS_PAGE_SIZE = 60;

/**
 * Query options of every file of a book.
 *
 * The key extends the generated key of the list, so the invalidation of the sources reaches it, and the extra part
 * keeps it apart from the one-page list the description reads under the generated key itself.
 */
function sourcesOptions(bookId: string) {
  return queryOptions({
    queryKey: [
      ...listSourcesApiV1BooksBookIdSourcesGetQueryKey({ path: { book_id: bookId } }),
      'all',
    ],
    queryFn: async ({ signal }): Promise<ImportedSource[]> => {
      const sources: ImportedSource[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listSourcesApiV1BooksBookIdSourcesGet({
          path: { book_id: bookId },
          query: { page, size: SOURCES_PAGE_SIZE },
          signal,
          throwOnError: true,
        });
        sources.push(...data.items.filter(isImported));
        total = data.pages;
        page += 1;
      }
      return sources;
    },
  });
}

/** Read every file of the book in the order of the book. */
export function useSources(bookId: string): UseQueryResult<ImportedSource[]> {
  return useQuery(sourcesOptions(bookId));
}

/**
 * Query options of the report of the imports: every file that was rejected or skipped and not yet closed.
 *
 * The key extends the generated key of the list, as that of the files does, so the invalidation of the sources reaches
 * it too: a source that was imported, an import that ended and the closing of the report all read it again.
 */
function reportOptions(bookId: string) {
  return queryOptions({
    queryKey: [
      ...listSourcesApiV1BooksBookIdSourcesGetQueryKey({ path: { book_id: bookId } }),
      'report',
    ],
    queryFn: async ({ signal }): Promise<SourceSchema[]> => {
      const files: SourceSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listSourcesApiV1BooksBookIdSourcesGet({
          path: { book_id: bookId },
          query: { page, size: SOURCES_PAGE_SIZE, status: REPORTED_STATUSES },
          signal,
          throwOnError: true,
        });
        files.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return files;
    },
  });
}

/** Read the files the imports of the book rejected or skipped, in the order they were recorded. */
export function useImportReport(bookId: string): UseQueryResult<SourceSchema[]> {
  return useQuery(reportOptions(bookId));
}

/** Close the report of the imports, which deletes the rejected and skipped files on the server. */
export function useCloseImportReport() {
  return useMutation(deleteUnimportedSourcesApiV1BooksBookIdSourcesUnimportedDeleteMutation());
}

/**
 * Read one page of the scans of a file, which the server lists in the order of the file.
 *
 * @param projectId The book.
 * @param sourceId The file, or undefined for none, which reads nothing.
 * @param page The page of the list, from 1.
 */
export function useScans(bookId: string, sourceId: string | undefined, page: number) {
  return useQuery({
    ...listScansApiV1BooksBookIdScansGetOptions({
      path: { book_id: bookId },
      query: { page, size: SCANS_PAGE_SIZE, source_id: sourceId },
    }),
    enabled: sourceId !== undefined,
  });
}

/** Read the latest jobs of the book, which the events of the jobs keep current. */
export function useJobs(bookId: string): UseQueryResult<JobSchema[]> {
  return useQuery(jobsOptions(bookId, false));
}
