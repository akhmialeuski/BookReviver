import { type QueryClient, useMutation, useQueryClient } from '@tanstack/react-query';
import type { PageSchema } from '@/api';
import {
  attachScanApiV1ProjectsProjectIdPagesPageIdScanPutMutation,
  createPageApiV1ProjectsProjectIdPagesPostMutation,
  deletePageApiV1ProjectsProjectIdPagesPageIdDeleteMutation,
  deleteSourceApiV1ProjectsProjectIdSourcesSourceIdDeleteMutation,
  movePagesApiV1ProjectsProjectIdPagesMovePostMutation,
  moveSourcePagesApiV1ProjectsProjectIdSourcesSourceIdPagesMovePostMutation,
  numberPagesApiV1ProjectsProjectIdPagesLabelsPostMutation,
  updatePageApiV1ProjectsProjectIdPagesPageIdPatchMutation,
} from '@/api/@tanstack/react-query.gen';
import { manifestOptions } from '@/features/pages/manifest';
import { anchorOf, movePages, pageIdsOfSource } from '@/features/pages/order';
import {
  invalidatePages,
  invalidateProject,
  invalidateProjectList,
  invalidateScans,
  invalidateSources,
} from '@/features/projects/queries';

/**
 * Every change a reader can make to the pages of a book, as TanStack Query mutations over the generated client.
 *
 * A move is applied to the cached manifest before the server answers and put back if it fails, so the strip never
 * lags behind a click. Whatever the outcome, the manifest is read again afterwards, which settles on the server's
 * order and makes a conflict show what the other change did. The other changes wait for the answer, since they
 * cannot be predicted: a numbering, a blank leaf whose image a job writes, a scan taken over from a page.
 * Each hook takes the book it changes, so a screen asks for exactly the actions it offers.
 */

interface Snapshot {
  previous: PageSchema[] | undefined;
}

/** Read the pages again, and the counts that depend on them. */
async function refreshPages(queryClient: QueryClient, projectId: string): Promise<void> {
  await Promise.all([
    invalidatePages(queryClient, projectId),
    invalidateProject(queryClient, projectId),
    invalidateProjectList(queryClient),
  ]);
}

/**
 * Write a new order into the cached manifest and return what was there, to put back if the move fails.
 *
 * @param newOrder Works the new order out of the pages now in the cache, or gives null for a move that cannot be
 * shown, in which case the cache is left as it is.
 */
async function applyOptimistically(
  queryClient: QueryClient,
  projectId: string,
  newOrder: (pages: PageSchema[]) => PageSchema[] | null,
): Promise<Snapshot> {
  const { queryKey } = manifestOptions(projectId);
  // A read that is still in flight would write the old order over this one when it lands
  await queryClient.cancelQueries({ queryKey });
  const previous = queryClient.getQueryData(queryKey);
  const next = previous === undefined ? null : newOrder(previous);
  if (next !== null) {
    queryClient.setQueryData(queryKey, next);
  }
  return { previous };
}

function restore(
  queryClient: QueryClient,
  projectId: string,
  snapshot: Snapshot | undefined,
): void {
  if (snapshot?.previous !== undefined) {
    queryClient.setQueryData(manifestOptions(projectId).queryKey, snapshot.previous);
  }
}

/** Move a page or a group of pages before or after another page. */
export function useMovePages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...movePagesApiV1ProjectsProjectIdPagesMovePostMutation(),
    onMutate: ({ body }) =>
      applyOptimistically(queryClient, projectId, (pages) => {
        const anchor = anchorOf(body);
        return anchor === null ? null : movePages(pages, body.page_ids, anchor);
      }),
    onError: (_error, _variables, snapshot) => restore(queryClient, projectId, snapshot),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Move every page of a source before or after another page. */
export function useMoveSourcePages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...moveSourcePagesApiV1ProjectsProjectIdSourcesSourceIdPagesMovePostMutation(),
    onMutate: ({ body, path }) =>
      applyOptimistically(queryClient, projectId, (pages) => {
        const anchor = anchorOf(body);
        return anchor === null
          ? null
          : movePages(pages, pageIdsOfSource(pages, path.source_id), anchor);
      }),
    onError: (_error, _variables, snapshot) => restore(queryClient, projectId, snapshot),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Change the label, kind, inclusion or notes of a page. */
export function useUpdatePage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...updatePageApiV1ProjectsProjectIdPagesPageIdPatchMutation(),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Write printed numbers into a range of pages. */
export function useNumberPages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...numberPagesApiV1ProjectsProjectIdPagesLabelsPostMutation(),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Add a placeholder or a blank leaf. */
export function useCreatePage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...createPageApiV1ProjectsProjectIdPagesPostMutation(),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Bind a scan to a placeholder. */
export function useAttachScan(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...attachScanApiV1ProjectsProjectIdPagesPageIdScanPutMutation(),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Delete a page with its versions, leaving its scan and source. */
export function useDeletePage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deletePageApiV1ProjectsProjectIdPagesPageIdDeleteMutation(),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Delete a source with its scans; the pages cut from it stay in the book. */
export function useDeleteSource(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteSourceApiV1ProjectsProjectIdSourcesSourceIdDeleteMutation(),
    onSettled: async () => {
      await Promise.all([
        refreshPages(queryClient, projectId),
        invalidateSources(queryClient, projectId),
        invalidateScans(queryClient, projectId),
      ]);
    },
  });
}
