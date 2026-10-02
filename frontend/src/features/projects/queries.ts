import type { QueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  listPagesApiV1ProjectsProjectIdPagesGetQueryKey,
  listProjectJobsApiV1ProjectsProjectIdJobsGetQueryKey,
  listProjectsApiV1ProjectsGetQueryKey,
  listScansApiV1ProjectsProjectIdScansGetQueryKey,
  listSourcesApiV1ProjectsProjectIdSourcesGetQueryKey,
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey,
  listStagesApiV1ProjectsProjectIdStagesGetQueryKey,
  projectApiV1ProjectsProjectIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { STAGES } from '@/features/stages/stages';

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

/** Refresh the summary of the stages of a book, which the stage bar draws. */
export function invalidateStageSummary(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listStagesApiV1ProjectsProjectIdStagesGetQueryKey({
      path: { project_id: projectId },
    }),
  });
}

/** Refresh the rows of one stage, where each page of the book stands in it, which the strip draws. */
export function invalidateStageRows(
  queryClient: QueryClient,
  projectId: string,
  stage: Stage,
): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey({
      path: { project_id: projectId, stage },
    }),
  });
}

/** Refresh the rows of every stage; only the stage on screen is read again at once, the others when it is opened. */
export async function invalidateAllStageRows(
  queryClient: QueryClient,
  projectId: string,
): Promise<void> {
  await Promise.all(STAGES.map(({ stage }) => invalidateStageRows(queryClient, projectId, stage)));
}

/** Refresh the jobs of a book, those running and the latest of any state, which the activity shows. */
export function invalidateJobs(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listProjectJobsApiV1ProjectsProjectIdJobsGetQueryKey({
      path: { project_id: projectId },
    }),
  });
}

/** Refresh the scans of a book, in every filter. */
export function invalidateScans(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listScansApiV1ProjectsProjectIdScansGetQueryKey({ path: { project_id: projectId } }),
  });
}
