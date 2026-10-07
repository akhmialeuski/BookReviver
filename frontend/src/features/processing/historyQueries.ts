import {
  type InfiniteData,
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import type { PagePageStepChangeSchema, PageStepChangeSchema, Stage } from '@/api';
import {
  clearHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdDeleteMutation,
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGetInfiniteOptions,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPostMutation,
} from '@/api/@tanstack/react-query.gen';
import type * as sdk from '@/api/sdk.gen';
import {
  invalidateStageRows,
  invalidateStageSummary,
  invalidateVersions,
} from '@/features/projects/queries';

/**
 * The history of a step on a page, the undo that takes its changes back and the clear that deletes it.
 *
 * Every change of a layer of a step is written to the history on the server, so what changes a page also changes its
 * history, and an undo changes the settings, the edits and the history of every page its batch reached. A clear takes
 * the settings and the edit of the step away from the page along with its history, and deletes its results with the
 * results that read them.
 */

/** How many changes the history loads at a time, which is the most the server gives a page of a list. */
export const HISTORY_PAGE_SIZE = 100;

// The generated client names each query by the function that makes it, so these are checked against the client
const HISTORY_QUERY: keyof typeof sdk =
  'listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet';
const SETTINGS_QUERY: keyof typeof sdk =
  'listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGet';
const EDITS_QUERY: keyof typeof sdk = 'listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGet';

// What an undo and a carry-over change: the histories, the settings and the edits of the pages
const UNDONE_QUERIES: ReadonlySet<unknown> = new Set([HISTORY_QUERY, SETTINGS_QUERY, EDITS_QUERY]);

function queryIdOf(key: readonly unknown[]): unknown {
  const head = key[0];
  return typeof head === 'object' && head !== null && '_id' in head ? head._id : undefined;
}

/** Mark the histories of the steps out of date, so the ones on screen are read again. */
export function invalidateHistory(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => queryIdOf(query.queryKey) === HISTORY_QUERY,
  });
}

/**
 * Mark the settings, the edits and the histories of the pages out of date, which a change that reaches several pages
 * writes to, so the ones on screen are read again.
 */
export function invalidatePageLayers(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => UNDONE_QUERIES.has(queryIdOf(query.queryKey)),
  });
}

/** The changes loaded so far, the newest first, and how many the step has on the page in all. */
export interface LoadedHistory {
  changes: readonly PageStepChangeSchema[];
  total: number;
}

function loadedOf(data: InfiniteData<PagePageStepChangeSchema>): LoadedHistory {
  return { changes: data.pages.flatMap((page) => page.items), total: data.pages[0]?.total ?? 0 };
}

/**
 * Read the changes of a step on a page, the newest first, a page of a hundred at a time, with the ones an undo took back
 * marked.
 *
 * The first request reads the newest changes and the total, and `fetchNextPage` reads the ones before them, which the
 * timeline asks for only when the list it shows needs rows beyond those loaded. A change that is written or taken back
 * reads every page that was loaded again, so the rows on screen stay the newest ones.
 */
export function usePageHistory(
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
  stepId: string | null,
) {
  return useInfiniteQuery({
    ...listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGetInfiniteOptions({
      path: { project_id: projectId, page_id: pageId ?? '', stage, step_id: stepId ?? '' },
      query: { size: HISTORY_PAGE_SIZE },
    }),
    initialPageParam: 1,
    getNextPageParam: (last, all) => (last.page < last.pages ? all.length + 1 : undefined),
    select: loadedOf,
    enabled: pageId !== undefined && stepId !== null,
  });
}

/**
 * What an undo and a clear change: the settings, the edits and the histories of the pages, and the rows and the
 * summary of the stage, which a change marks out of date on the server.
 */
function invalidateAfterHistoryChange(
  queryClient: QueryClient,
  projectId: string,
  stage: Stage,
): Promise<unknown[]> {
  return Promise.all([
    invalidatePageLayers(queryClient),
    invalidateStageRows(queryClient, projectId, stage),
    invalidateStageSummary(queryClient, projectId),
  ]);
}

/**
 * Take back the newest change of a step on a page, or every change back to a chosen one.
 *
 * The undo joins the mutation scope of the edits of the book, so it reaches the server after the edit that was saved
 * just before it. It marks the stage out of date on the server, and the rows and the summary of the stage, the
 * settings, the edits and the histories of the pages are read again, since a batch is taken back on every page it
 * reached.
 */
export function useUndo(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPostMutation(),
    scope: { id: `page-edits:${projectId}` },
    onSettled: () => invalidateAfterHistoryChange(queryClient, projectId, stage),
  });
}

/**
 * Return a step to its initial state on a page: delete its history, its settings, its edit and its results.
 *
 * The clear joins the mutation scope of the edits of the book like an undo, and reads again what an undo does, and
 * the results of the page too, since the server deleted the results of the step and the ones that read them.
 */
export function useClearHistory(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...clearHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdDeleteMutation(),
    scope: { id: `page-edits:${projectId}` },
    onSettled: (_cleared, _error, { path }) =>
      Promise.all([
        invalidateAfterHistoryChange(queryClient, projectId, stage),
        invalidateVersions(queryClient, projectId, path.page_id),
      ]),
  });
}
