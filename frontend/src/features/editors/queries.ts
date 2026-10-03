import { useMutation, useQuery } from '@tanstack/react-query';
import type { PageEditSchema, Stage } from '@/api';
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
 * were made and an older save never lands over a newer one.
 */

/** The query key of the edits of one stage of a page. */
export function editsKey(projectId: string, pageId: string, stage: Stage) {
  return listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetQueryKey({
    path: { project_id: projectId, page_id: pageId, stage },
  });
}

/** Read the manual edits a stage has on a page. */
export function useEdits(
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
  enabled: boolean,
): PageEditSchema[] | undefined {
  const { data } = useQuery({
    ...listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetOptions({
      path: { project_id: projectId, page_id: pageId ?? '', stage },
    }),
    select: (page) => page.items,
    enabled: enabled && pageId !== undefined,
  });
  return data;
}

/** The changes that save and delete an edit. */
export function useEditChanges(projectId: string) {
  const scope = { id: `page-edits:${projectId}` };
  return {
    save: useMutation({
      ...putEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdPutMutation(),
      scope,
    }),
    remove: useMutation({
      ...deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdDeleteMutation(),
      scope,
    }),
  };
}
