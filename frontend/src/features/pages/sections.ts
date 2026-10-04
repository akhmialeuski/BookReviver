import { queryOptions, type UseQueryResult, useQuery } from '@tanstack/react-query';
import {
  listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGet,
  type PaginationSectionSchema,
} from '@/api';
import { listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGetQueryKey } from '@/api/@tanstack/react-query.gen';

/**
 * The pagination sections of a book as one query, in book order.
 *
 * The server answers a page of at most a hundred sections per request, and the panel and the grid need all of them, so
 * the query reads every request and keeps the sections as one list. Its key is the generated key of the endpoint, so
 * `invalidateSections` of the queries module, which the event stream and the changes of pages call, refreshes it.
 */

/** Sections asked for per request, which is the most the route accepts. */
export const SECTIONS_PAGE_SIZE = 100;

/** Query options of every section of a book. */
export function sectionsOptions(projectId: string) {
  return queryOptions({
    queryKey: listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGetQueryKey({
      path: { project_id: projectId },
      query: { size: SECTIONS_PAGE_SIZE },
    }),
    queryFn: async ({ signal }): Promise<PaginationSectionSchema[]> => {
      const sections: PaginationSectionSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGet({
          path: { project_id: projectId },
          query: { page, size: SECTIONS_PAGE_SIZE },
          signal,
          throwOnError: true,
        });
        sections.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return sections;
    },
  });
}

/** Read the pagination sections of a book in book order. */
export function useSections(projectId: string): UseQueryResult<PaginationSectionSchema[]> {
  return useQuery(sectionsOptions(projectId));
}
