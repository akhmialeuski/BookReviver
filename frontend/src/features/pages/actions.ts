import { type QueryClient, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  createPagesApiV1ProjectsProjectIdPagesBatchPost,
  deletePageApiV1ProjectsProjectIdPagesPageIdDelete,
  type PageCreate,
  type PageSchema,
  type PageUpdate,
  updatePageApiV1ProjectsProjectIdPagesPageIdPatch,
} from '@/api';
import {
  attachScanApiV1ProjectsProjectIdPagesPageIdScanPutMutation,
  createPaginationSectionApiV1ProjectsProjectIdPaginationSectionsPostMutation,
  deletePageApiV1ProjectsProjectIdPagesPageIdDeleteMutation,
  deletePaginationSectionApiV1ProjectsProjectIdPaginationSectionsSectionIdDeleteMutation,
  deleteSourceApiV1ProjectsProjectIdSourcesSourceIdDeleteMutation,
  fillBlankPagesApiV1ProjectsProjectIdPagesBlankFillPostMutation,
  movePagesApiV1ProjectsProjectIdPagesMovePostMutation,
  moveSourcePagesApiV1ProjectsProjectIdSourcesSourceIdPagesMovePostMutation,
  numberPagesApiV1ProjectsProjectIdPagesLabelsPostMutation,
  putPaginationSectionApiV1ProjectsProjectIdPaginationSectionsSectionIdPutMutation,
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
  invalidateSections,
  invalidateSources,
  pageChangesInFlight,
  pagesScope,
} from '@/features/projects/queries';

/**
 * Every change a reader can make to the pages of a book, as TanStack Query mutations over the generated client.
 *
 * A move, a change of the kind, inclusion, number or notes of pages, and the choice of the image of blank pages, is
 * applied to the cached manifest before the server answers and put back if it fails, so the grid never lags behind a click. Whatever the outcome, the
 * manifest is read again afterwards, which settles on the server's state and makes a conflict show what the other
 * change did. The other changes wait for the answer, since they cannot be predicted: a numbering, a blank leaf whose
 * image a job writes, a scan taken over from a page.
 * Each hook takes the book it changes, so a screen asks for exactly the actions it offers.
 */

interface Snapshot {
  previous: PageSchema[] | undefined;
}

/**
 * Read the pages again, and the counts that depend on them, unless a change of the pages is still to come.
 *
 * A read now would show the pages without the change that is queued or in flight, and write that over the page order
 * the grid is showing, which flips a page back for as long as the change takes. Each change reads the pages again when
 * it ends, so the last one to end makes the read that matters.
 *
 * @param settling How many of the changes in flight are the one that calls this, which is one for a change that runs in
 * the scope of the pages and none for one that does not.
 */
async function refreshPages(
  queryClient: QueryClient,
  projectId: string,
  settling = 0,
): Promise<void> {
  if (pageChangesInFlight(queryClient, projectId) > settling) {
    return;
  }
  await Promise.all([
    invalidatePages(queryClient, projectId),
    // A page that leaves hands its section on, so a section can change with the pages
    invalidateSections(queryClient, projectId),
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
  // A read that is still in flight would write the old state over this one when it lands. It is cancelled with the
  // default revert, which puts the query back to a success with the data it had. Cancelling without the revert leaves
  // the query in the error state, and a screen that shows an error in place of the pages loses its grid until the
  // next read ends
  await queryClient.cancelQueries({ queryKey });
  const previous = queryClient.getQueryData(queryKey);
  const next = previous === undefined ? null : newOrder(previous);
  if (next !== null) {
    queryClient.setQueryData(queryKey, next);
  }
  return { previous };
}

/**
 * The scope of the changes that rewrite pages that exist. A reader changing the number, the kind and the notes of a
 * page in a row makes two changes of one page in flight together, and a move on top of an edit would too.
 */
const rewriteScope = pagesScope;

/** What a change that runs in the scope of the pages does when it ends: read the pages once the rest have ended. */
function readWhenLast(queryClient: QueryClient, projectId: string): () => Promise<void> {
  return () => refreshPages(queryClient, projectId, 1);
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
    onSettled: readWhenLast(queryClient, projectId),
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
    onSettled: readWhenLast(queryClient, projectId),
  });
}

/** Change the label, kind, inclusion or notes of a page. */
export function useUpdatePage(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...updatePageApiV1ProjectsProjectIdPagesPageIdPatchMutation(),
    scope: rewriteScope(projectId),
    onSettled: readWhenLast(queryClient, projectId),
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
    onSettled: readWhenLast(queryClient, projectId),
  });
}

/**
 * Choose the image of blank pages: their scan, a white leaf or the paper of the book.
 *
 * The choice is shown on the pages at once. The leaf itself is drawn by a job, so the picture of a page changes when
 * the manifest is read after the job's event, and a page the server refuses leaves all of them as they were.
 */
export function useFillBlankPages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...fillBlankPagesApiV1ProjectsProjectIdPagesBlankFillPostMutation(),
    scope: rewriteScope(projectId),
    onMutate: ({ body }) =>
      applyOptimistically(queryClient, projectId, (pages) => {
        const targets = new Set(body.page_ids);
        return pages.map((page) =>
          targets.has(page.id) ? { ...page, blank_fill: body.blank_fill } : page,
        );
      }),
    onError: (_error, _variables, snapshot) => restore(queryClient, projectId, snapshot),
    onSettled: readWhenLast(queryClient, projectId),
  });
}

/**
 * Add several placeholders or blank leaves in one request, so that pages put before the same page stand in the order
 * they are listed.
 *
 * The server adds all of them or none, so a page it refuses leaves the book as it was. The request runs in the scope of
 * the pages, behind the changes already queued, and the manifest is read once when the last of them has ended. Outside
 * the scope, the read that an earlier change makes when it ends could start before the new pages are stored, and the
 * request would then find that change still running and leave the reading to it.
 */
export function useCreatePages(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    scope: rewriteScope(projectId),
    mutationFn: async (bodies: readonly PageCreate[]): Promise<void> => {
      await createPagesApiV1ProjectsProjectIdPagesBatchPost({
        path: { project_id: projectId },
        body: [...bodies],
        throwOnError: true,
      });
    },
    onSettled: readWhenLast(queryClient, projectId),
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
    onSettled: readWhenLast(queryClient, projectId),
  });
}

/**
 * Make a pagination section, which renumbers the pages it takes.
 *
 * The labels are written by the server in the transaction of the change, so nothing is shown before it answers. The
 * change runs in the scope of the pages, like every change that rewrites them, and the manifest and the sections are
 * read again when the last of them ends.
 */
export function useCreateSection(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...createPaginationSectionApiV1ProjectsProjectIdPaginationSectionsPostMutation(),
    scope: rewriteScope(projectId),
    onSettled: readWhenLast(queryClient, projectId),
  });
}

/** Replace the first page and the rule of a pagination section, which renumbers the pages it takes. */
export function usePutSection(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putPaginationSectionApiV1ProjectsProjectIdPaginationSectionsSectionIdPutMutation(),
    scope: rewriteScope(projectId),
    onSettled: readWhenLast(queryClient, projectId),
  });
}

/** Remove a pagination section, which hands its pages to the section before it and renumbers them. */
export function useDeleteSection(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deletePaginationSectionApiV1ProjectsProjectIdPaginationSectionsSectionIdDeleteMutation(),
    scope: rewriteScope(projectId),
    onSettled: readWhenLast(queryClient, projectId),
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
