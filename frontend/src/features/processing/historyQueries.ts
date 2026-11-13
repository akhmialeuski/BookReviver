import {
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query';
import type { PageHistoryEntrySchema, PagePageHistoryEntrySchema, Stage } from '@/api';
import {
  clearHistoryApiV1BooksBookIdPagesPageIdHistoryStageStepIdDeleteMutation,
  listHistoryApiV1BooksBookIdPagesPageIdHistoryStageStepIdGetInfiniteOptions,
  undoChangeApiV1BooksBookIdPagesPageIdHistoryStageStepIdUndoPostMutation,
} from '@/api/@tanstack/react-query.gen';
import { invalidateJobs, invalidatePageLayers } from '@/features/books/queries';

/**
 * The history of a step on a page, the undo that takes its changes back and the clear that deletes it.
 *
 * Every change of a layer of a step is written to the history on the server, so what changes a page also changes its
 * history, and an undo changes the settings, the edits and the history of every page its batch reached. A clear takes
 * the settings and the edit of the step away from the page along with its history, and deletes its results with the
 * results that read them.
 */

/** How many changes the history loads at a time, which is the most the server gives a page of a list. */
const HISTORY_PAGE_SIZE = 100;

/** The changes loaded so far, the newest first, and how many the step has on the page in all. */
interface LoadedHistory {
  changes: readonly PageHistoryEntrySchema[];
  total: number;
}

function loadedOf(data: InfiniteData<PagePageHistoryEntrySchema>): LoadedHistory {
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
  bookId: string,
  pageId: string | undefined,
  stage: Stage,
  stepId: string | null,
) {
  return useInfiniteQuery({
    ...listHistoryApiV1BooksBookIdPagesPageIdHistoryStageStepIdGetInfiniteOptions({
      path: { book_id: bookId, page_id: pageId ?? '', stage, step_id: stepId ?? '' },
      query: { size: HISTORY_PAGE_SIZE },
    }),
    initialPageParam: 1,
    getNextPageParam: (last, all) => (last.page < last.pages ? all.length + 1 : undefined),
    select: loadedOf,
    enabled: pageId !== undefined && stepId !== null,
  });
}

/**
 * Take back the newest change of a step on a page, or every change back to a chosen one.
 *
 * The undo joins the mutation scope of the edits of the book, so it reaches the server after the edit that was saved
 * just before it. The settings, the edits and the histories of the pages are read again, since a batch is taken back
 * on every page it reached. It marks the stage out of date on the server, and the rows and the summary of the stage
 * follow the `book-changed` event.
 */
export function useUndo(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...undoChangeApiV1BooksBookIdPagesPageIdHistoryStageStepIdUndoPostMutation(),
    scope: { id: `page-edits:${bookId}` },
    onSettled: () => invalidatePageLayers(queryClient),
  });
}

/**
 * Return a step to its initial state on a page: delete its history, its settings, its edit and its results.
 *
 * The clear is a job of the book, which waits behind the jobs the book has queued or running, so the answer is the job
 * and the list of jobs is read again. The clear joins the mutation scope of the edits of the book like an undo, so it
 * reaches the server after the edit saved just before it. The history, the settings and the results of the page follow
 * the `book-changed` event the job sends when it has deleted them.
 */
export function useClearHistory(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...clearHistoryApiV1BooksBookIdPagesPageIdHistoryStageStepIdDeleteMutation(),
    scope: { id: `page-edits:${bookId}` },
    onSettled: () => invalidateJobs(queryClient, bookId),
  });
}
