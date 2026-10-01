import type { QueryClient } from '@tanstack/react-query';
import {
  listPagesApiV1ProjectsProjectIdPagesGetQueryKey,
  listProjectsApiV1ProjectsGetQueryKey,
  listScansApiV1ProjectsProjectIdScansGetQueryKey,
  listSourcesApiV1ProjectsProjectIdSourcesGetQueryKey,
  projectApiV1ProjectsProjectIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';

/**
 * Which cached queries a change of a book makes stale.
 *
 * The keys come from the generated client, and a key without parameters matches every query of its endpoint, so
 * one call refreshes all pages and filters of a list. The event stream and the mutations both call these.
 */

/** Refresh the list of books, whose counts and order change when a book is created, deleted or imported into. */
export function invalidateProjectList(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({ queryKey: listProjectsApiV1ProjectsGetQueryKey() });
}

/** Refresh the book itself with its counts. */
export function invalidateProject(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: projectApiV1ProjectsProjectIdGetQueryKey({ path: { project_id: projectId } }),
  });
}

/** Refresh the sources of a book. */
export function invalidateSources(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listSourcesApiV1ProjectsProjectIdSourcesGetQueryKey({
      path: { project_id: projectId },
    }),
  });
}

/** Refresh the pages of a book, in every request shape the manifest has been read with. */
export function invalidatePages(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listPagesApiV1ProjectsProjectIdPagesGetQueryKey({ path: { project_id: projectId } }),
  });
}

/** Refresh the scans of a book, in every filter. */
export function invalidateScans(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listScansApiV1ProjectsProjectIdScansGetQueryKey({ path: { project_id: projectId } }),
  });
}
