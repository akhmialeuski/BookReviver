import type { QueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  getRecipeApiV1ProjectsProjectIdStagesStageRecipeGetQueryKey,
  listPagesApiV1ProjectsProjectIdPagesGetQueryKey,
  listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGetQueryKey,
  listProjectJobsApiV1ProjectsProjectIdJobsGetQueryKey,
  listProjectsApiV1ProjectsGetQueryKey,
  listScansApiV1ProjectsProjectIdScansGetQueryKey,
  listSourcesApiV1ProjectsProjectIdSourcesGetQueryKey,
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey,
  listStagesApiV1ProjectsProjectIdStagesGetQueryKey,
  listVariantsApiV1ProjectsProjectIdStagesStageVariantsGetQueryKey,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey,
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

/**
 * The mutation scope of the changes that rewrite pages that exist, which TanStack Query runs one after the other.
 *
 * The server reads a page, changes it and writes the whole row, so two changes of one page that are in flight together
 * each write back the page as it was before the other, and the first change is lost.
 */
export function pagesScope(projectId: string): { id: string } {
  return { id: `pages-${projectId}` };
}

/**
 * Count the changes that rewrite pages of a book and are queued or in flight.
 *
 * While there are some, the pages read from the server lack a change that the screen already shows, so a read must
 * wait, and the last of the changes reads the pages when it ends.
 */
export function pageChangesInFlight(queryClient: QueryClient, projectId: string): number {
  const { id } = pagesScope(projectId);
  return queryClient.isMutating({ predicate: (mutation) => mutation.options.scope?.id === id });
}

/** Refresh the pages of a book, in every request shape the manifest has been read with. */
export function invalidatePages(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listPagesApiV1ProjectsProjectIdPagesGetQueryKey({ path: { project_id: projectId } }),
  });
}

/** Refresh the pagination sections of a book, which a change of its pages can move, hand over or renumber. */
export function invalidateSections(queryClient: QueryClient, projectId: string): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGetQueryKey({
      path: { project_id: projectId },
    }),
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

/** What the event of a ready version leaves in the cache for the page it is about. */
export interface VersionReady {
  versionId: string;
  /** When the event arrived, so a version announced twice is still a change the screen waiting for it sees. */
  at: number;
}

/** The key under which the last version announced ready for a page is kept, which a preview waits on. */
export function versionReadyKey(projectId: string, pageId: string): readonly string[] {
  return ['version-ready', projectId, pageId];
}

/** Refresh the results of a page, which a run adds to when its version is ready. */
export function invalidateVersions(
  queryClient: QueryClient,
  projectId: string,
  pageId: string,
): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey({
      path: { project_id: projectId, page_id: pageId },
    }),
  });
}

/** Refresh the recipes of a stage, which a job that writes their parameters, such as the measure of a book, changed. */
export function invalidateRecipes(
  queryClient: QueryClient,
  projectId: string,
  stage: Stage,
): Promise<void> {
  const path = { project_id: projectId, stage };
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: getRecipeApiV1ProjectsProjectIdStagesStageRecipeGetQueryKey({ path }),
    }),
    queryClient.invalidateQueries({
      queryKey: listVariantsApiV1ProjectsProjectIdStagesStageVariantsGetQueryKey({ path }),
    }),
  ]).then(() => undefined);
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
