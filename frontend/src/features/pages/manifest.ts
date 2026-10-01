import { queryOptions, type UseQueryResult, useQuery } from '@tanstack/react-query';
import { listPagesApiV1ProjectsProjectIdPagesGet, type PageSchema } from '@/api';
import { listPagesApiV1ProjectsProjectIdPagesGetQueryKey } from '@/api/@tanstack/react-query.gen';

/**
 * The page manifest of a book as one query shared by the page strip and the viewer.
 *
 * The server answers up to a thousand pages per request, and the viewer needs the whole book to turn pages, show
 * the slider and find the page a link names, so the query reads every request of the manifest and keeps the pages
 * as one list in book order. Its key is the generated key of the manifest endpoint, so `invalidatePages` of the
 * queries module, which the event stream calls, refreshes it. The pages of a book change by events and by the user's own
 * edits, which write into this cache before the server answers.
 */

/** Pages asked for per request; the manifest route accepts at most this many. */
export const MANIFEST_PAGE_SIZE = 1000;

/** Query options of the whole manifest of a book, every page included or excluded. */
export function manifestOptions(projectId: string) {
  return queryOptions({
    queryKey: listPagesApiV1ProjectsProjectIdPagesGetQueryKey({
      path: { project_id: projectId },
      query: { size: MANIFEST_PAGE_SIZE },
    }),
    queryFn: async ({ signal }): Promise<PageSchema[]> => {
      const pages: PageSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listPagesApiV1ProjectsProjectIdPagesGet({
          path: { project_id: projectId },
          query: { page, size: MANIFEST_PAGE_SIZE },
          signal,
          throwOnError: true,
        });
        pages.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return pages;
    },
  });
}

/** Read the pages of a book in book order. */
export function useManifest(projectId: string): UseQueryResult<PageSchema[]> {
  return useQuery(manifestOptions(projectId));
}
