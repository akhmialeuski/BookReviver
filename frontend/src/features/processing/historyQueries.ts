import { type QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGetOptions,
  undoChangeApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdUndoPostMutation,
} from '@/api/@tanstack/react-query.gen';
import type * as sdk from '@/api/sdk.gen';
import { invalidateStageRows, invalidateStageSummary } from '@/features/projects/queries';

/**
 * The history of a step on a page and the undo that takes its changes back.
 *
 * Every change of a layer of a step is written to the history on the server, so what changes a page also changes its
 * history, and an undo changes the settings, the edits and the history of every page its batch reached.
 */

const LIST_SIZE = 100;

// The generated client names each query by the function that makes it, so these are checked against the client
const HISTORY_QUERY: keyof typeof sdk =
  'listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGet';
const SETTINGS_QUERY: keyof typeof sdk =
  'listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGet';
const EDITS_QUERY: keyof typeof sdk = 'listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGet';

// What an undo changes: the histories, the settings and the edits of the pages
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

/** Read the changes of a step on a page, the newest first, with the ones an undo took back marked. */
export function usePageHistory(
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
  stepId: string | null,
) {
  return useQuery({
    ...listHistoryApiV1ProjectsProjectIdPagesPageIdHistoryStageStepIdGetOptions({
      path: { project_id: projectId, page_id: pageId ?? '', stage, step_id: stepId ?? '' },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled: pageId !== undefined && stepId !== null,
  });
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
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({
          predicate: (query) => UNDONE_QUERIES.has(queryIdOf(query.queryKey)),
        }),
        invalidateStageRows(queryClient, projectId, stage),
        invalidateStageSummary(queryClient, projectId),
      ]),
  });
}
