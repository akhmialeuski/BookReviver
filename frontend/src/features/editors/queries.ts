import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Stage } from '@/api';
import {
  deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdDeleteMutation,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetOptions,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetQueryKey,
  putEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdPutMutation,
} from '@/api/@tanstack/react-query.gen';

/**
 * The manual edits of a page in one stage, and the changes that save or delete one.
 *
 * Saving an edit marks the stage of the page out of date on the server and processes nothing, so the callers run the
 * stage themselves. The changes of edits share one mutation scope per book, so they reach the server in the order they
 * were made and an older save never lands over a newer one. A change reads the edits of its page again before it ends,
 * so a caller that awaits it sees the edit the server holds.
 */

/** Edits asked for per request; a page has one per step at most, and the route accepts at most this many. */
const EDITS_PAGE_SIZE = 100;

/** Read the manual edits a stage has on a page, one per step. */
export function useEdits(
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
  enabled = true,
) {
  return useQuery({
    ...listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetOptions({
      path: { project_id: projectId, page_id: pageId ?? '', stage },
      query: { size: EDITS_PAGE_SIZE },
    }),
    select: (page) => page.items,
    enabled: enabled && pageId !== undefined,
  });
}

/** The changes that save and delete an edit. */
export function useEditChanges(projectId: string) {
  const queryClient = useQueryClient();
  const scope = { id: `page-edits:${projectId}` };
  const onSettled = (
    _data: unknown,
    _error: unknown,
    { path }: { path: { project_id: string; page_id: string; stage: Stage } },
  ) =>
    queryClient.invalidateQueries({
      queryKey: listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetQueryKey({
        path: { project_id: path.project_id, page_id: path.page_id, stage: path.stage },
      }),
    });
  return {
    save: useMutation({
      ...putEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdPutMutation(),
      scope,
      onSettled,
    }),
    remove: useMutation({
      ...deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdDeleteMutation(),
      scope,
      onSettled,
    }),
  };
}
