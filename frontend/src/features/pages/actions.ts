import { type QueryClient, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  createPageApiV1ProjectsProjectIdPagesPost,
  deletePageApiV1ProjectsProjectIdPagesPageIdDelete,
  type PageCreate,
  type PageSchema,
  type PageUpdate,
  updatePageApiV1ProjectsProjectIdPagesPageIdPatch,
} from '@/api';
import {
  attachScanApiV1ProjectsProjectIdPagesPageIdScanPutMutation,
  deletePageApiV1ProjectsProjectIdPagesPageIdDeleteMutation,
  deleteSourceApiV1ProjectsProjectIdSourcesSourceIdDeleteMutation,
  movePagesApiV1ProjectsProjectIdPagesMovePostMutation,
  moveSourcePagesApiV1ProjectsProjectIdSourcesSourceIdPagesMovePostMutation,
  numberPagesApiV1ProjectsProjectIdPagesLabelsPostMutation,
  updatePageApiV1ProjectsProjectIdPagesPageIdPatchMutation,
} from '@/api/@tanstack/react-query.gen';
import { applyChanges } from '@/features/pages/edits';
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
 * A move, and a change of the kind, inclusion, number or notes of pages, is applied to the cached manifest before
 * the server answers and put back if it fails, so the grid never lags behind a click. Whatever the outcome, the
 * manifest is read again afterwards, which settles on the server's state and makes a conflict show what the other
 * change did. The other changes wait for the answer, since they cannot be predicted: a numbering, a blank leaf whose
 * image a job writes, a scan taken over from a page.
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
  const previous = queryClient.getQueryData(queryKey);
  const next = previous === undefined ? null : newOrder(previous);
  // Written before anything is awaited, so a control that shows this state never has to wait for a read to give way
  if (next !== null) {
    queryClient.setQueryData(queryKey, next);
  }
  // A read that is still in flight would write the old state over this one when it lands. Cancelling it must not
  // revert the cache to what it held when the read began, which would take this write away too
  await queryClient.cancelQueries({ queryKey }, { revert: false });
  return { previous };
}

/**
 * The scope of the changes that rewrite pages that exist, which TanStack Query runs one after the other.
 *
 * The server reads a page, changes it and writes the whole row, so two changes of one page that are in flight together
 * each write back the page as it was before the other, and the first change is lost. A reader changing the number, the
 * kind and the notes of a page in a row makes exactly that, and a move on top of an edit would too.
 */
function rewriteScope(projectId: string): { id: string } {
  return { id: `pages-${projectId}` };
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
    scope: rewriteScope(projectId),
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
    scope: rewriteScope(projectId),
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
    scope: rewriteScope(projectId),
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/**
 * Change the same fields of several pages at once, as the panel of selected pages does.
 *
 * The change is shown on the pages at once, and put back if a request fails. The requests go out together and the
 * manifest is read once when all have answered. When one fails the others have still been made, and the read
 * afterwards shows which pages changed.
 */
export function useUpdatePages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    scope: rewriteScope(projectId),
    onMutate: ({ pageIds, changes }) =>
      applyOptimistically(queryClient, projectId, (pages) => {
        const targets = new Set(pageIds);
        return pages.map((page) => (targets.has(page.id) ? applyChanges(page, changes) : page));
      }),
    onError: (_error, _variables, snapshot) => restore(queryClient, projectId, snapshot),
    mutationFn: async ({
      pageIds,
      changes,
    }: {
      pageIds: readonly string[];
      changes: PageUpdate;
    }): Promise<void> => {
      await Promise.all(
        pageIds.map((pageId) =>
          updatePageApiV1ProjectsProjectIdPagesPageIdPatch({
            path: { project_id: projectId, page_id: pageId },
            body: changes,
            throwOnError: true,
          }),
        ),
      );
    },
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/**
 * Add several placeholders or blank leaves one after the other, so that pages put before the same page stand in the
 * order they are listed.
 *
 * The manifest is read once when the last request has answered or one has failed.
 */
export function useCreatePages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (bodies: readonly PageCreate[]): Promise<void> => {
      for (const body of bodies) {
        await createPageApiV1ProjectsProjectIdPagesPost({
          path: { project_id: projectId },
          body,
          throwOnError: true,
        });
      }
    },
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Delete several pages with their versions, leaving their scans and sources. */
export function useDeletePages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (pageIds: readonly string[]): Promise<void> => {
      await Promise.all(
        pageIds.map((pageId) =>
          deletePageApiV1ProjectsProjectIdPagesPageIdDelete({
            path: { project_id: projectId, page_id: pageId },
            throwOnError: true,
          }),
        ),
      );
    },
    onSettled: () => refreshPages(queryClient, projectId),
  });
}

/** Write printed numbers into a range of pages. */
export function useNumberPages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...numberPagesApiV1ProjectsProjectIdPagesLabelsPostMutation(),
    scope: rewriteScope(projectId),
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
