import { queryOptions, type UseQueryResult, useQuery } from '@tanstack/react-query';
import {
  type JobSchema,
  listSourcesApiV1ProjectsProjectIdSourcesGet,
  type SourceSchema,
} from '@/api';
import {
  listScansApiV1ProjectsProjectIdScansGetOptions,
  listSourcesApiV1ProjectsProjectIdSourcesGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { isActiveJob } from '@/features/projects/events';
import { jobsOptions } from '@/features/workspace/queries';

/**
 * The queries behind the Import stage: every file of the book as one list, and the jobs that import files into it.
 *
 * Both are marked stale by the event stream through the generated keys, as the manifest of pages is.
 */

/** Files asked for per request; the route accepts at most this many. */
const SOURCES_PAGE_SIZE = 100;

/** Scans asked for per page of the grid; the route accepts at most 100. */
const SCANS_PAGE_SIZE = 60;

/** How often a book with a running import reads its jobs again, a safety net for an event stream that was cut. */
const POLL_INTERVAL_MS = 5000;

/**
 * Query options of every file of a book.
 *
 * The key extends the generated key of the list, so the invalidation of the sources reaches it, and the extra part
 * keeps it apart from the one-page list the description reads under the generated key itself.
 */
function sourcesOptions(projectId: string) {
  return queryOptions({
    queryKey: [
      ...listSourcesApiV1ProjectsProjectIdSourcesGetQueryKey({ path: { project_id: projectId } }),
      'all',
    ],
    queryFn: async ({ signal }): Promise<SourceSchema[]> => {
      const sources: SourceSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listSourcesApiV1ProjectsProjectIdSourcesGet({
          path: { project_id: projectId },
          query: { page, size: SOURCES_PAGE_SIZE },
          signal,
          throwOnError: true,
        });
        sources.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return sources;
    },
  });
}

/** Read every file of the book in the order of the book. */
export function useSources(projectId: string): UseQueryResult<SourceSchema[]> {
  return useQuery(sourcesOptions(projectId));
}

/**
 * Read one page of the scans of a file, which the server lists in the order of the file.
 *
 * @param projectId The book.
 * @param sourceId The file, or undefined for none, which reads nothing.
 * @param page The page of the list, from 1.
 */
export function useScans(projectId: string, sourceId: string | undefined, page: number) {
  return useQuery({
    ...listScansApiV1ProjectsProjectIdScansGetOptions({
      path: { project_id: projectId },
      query: { page, size: SCANS_PAGE_SIZE, source_id: sourceId },
    }),
    enabled: sourceId !== undefined,
  });
}

/** Read the latest jobs of the book, and keep reading them while one is queued or running. */
export function useJobs(projectId: string): UseQueryResult<JobSchema[]> {
  return useQuery({
    ...jobsOptions(projectId, false),
    refetchInterval: (query) => (query.state.data?.some(isActiveJob) ? POLL_INTERVAL_MS : false),
  });
}
