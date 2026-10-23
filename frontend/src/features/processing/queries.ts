import {
  queryOptions,
  type UseQueryResult,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  type CarryOverSchema,
  listScansApiV1BooksBookIdScansGet,
  listVersionsApiV1BooksBookIdPagesPageIdVersionsGet,
  type PageVersionSchema,
  type RunImpactSchema,
  runImpactApiV1BooksBookIdStagesStageRunImpactPost,
  type ScanSchema,
  type Stage,
  type StageRunBody,
} from '@/api';
import {
  carryOverEditApiV1BooksBookIdPagesPageIdEditsStageStepIdCarryOverPostMutation,
  chooseVersionApiV1BooksBookIdPagesPageIdStagesStagePutMutation,
  collectableVersionsApiV1BooksBookIdVersionsCollectableGetOptions,
  collectVersionsApiV1BooksBookIdVersionsCollectPostMutation,
  deleteValueApiV1BooksBookIdStagesStageStepsStepIdValuesNameDeleteMutation,
  detectContentTypesApiV1BooksBookIdPagesContentTypesDetectPostMutation,
  listProcessorsApiV1ProcessorsGetOptions,
  listRecipesApiV1BooksBookIdStagesStageRecipesGetOptions,
  listScansApiV1BooksBookIdScansGetQueryKey,
  listSettingsApiV1BooksBookIdPagesPageIdSettingsStageGetOptions,
  listVersionsApiV1BooksBookIdPagesPageIdVersionsGetQueryKey,
  measureBookApiV1BooksBookIdStagesGeometryMeasurePostMutation,
  previewStepApiV1BooksBookIdStagesStagePreviewPostMutation,
  putMarkApiV1BooksBookIdPagesPageIdVersionsVersionIdMarkPutMutation,
  putRecipeApiV1BooksBookIdStagesStageRecipesRecipeIdPutMutation,
  putValueApiV1BooksBookIdStagesStageStepsStepIdValuesNamePutMutation,
  resetRecipeApiV1BooksBookIdStagesStageRecipesRecipeIdResetPostMutation,
  runImpactApiV1BooksBookIdStagesStageRunImpactPostMutation,
  runStageApiV1BooksBookIdStagesStageRunPostMutation,
} from '@/api/@tanstack/react-query.gen';
import {
  invalidateAllRecipes,
  invalidateJobs,
  invalidateStageRows,
  invalidateVersions,
} from '@/features/books/queries';
import { invalidatePageLayers } from '@/features/processing/historyQueries';

/**
 * The queries and changes behind the processing workspace: the catalogue of processors, the recipes of a stage, the
 * versions of a page, and the saving, running, previewing and choosing that act on them.
 *
 * The keys are the generated ones. A recipe is always saved through the route of its id. A change reads again only what
 * the control that made it shows, so that control never shows the state before it. The rows and the summary of the
 * stages, which a save marks out of date on the server, are read again by the `book-changed` event the change sends.
 */

/** Items asked for per request of a short list; the routes accept at most this many. */
const LIST_SIZE = 100;

/** Query options of the catalogue of processors, which the screens and the router read through one key. */
export function processorsOptions() {
  return queryOptions({
    ...listProcessorsApiV1ProcessorsGetOptions({ query: { size: LIST_SIZE } }),
    // The installed plugins change with a restart of the server, not while a screen is open
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Read the catalogue of processors, which a stage is built from. */
export function useProcessors() {
  return useQuery({ ...processorsOptions(), select: (page) => page.items });
}

/** Query options of the recipes of a stage, which the screens and the router read through one key. */
export function recipesOptions(bookId: string, stage: Stage) {
  return listRecipesApiV1BooksBookIdStagesStageRecipesGetOptions({
    path: { book_id: bookId, stage },
    query: { size: LIST_SIZE },
  });
}

/** Read the recipes of a stage, one for each kind of page, in the order of the kinds. */
export function useRecipes(bookId: string, stage: Stage, enabled: boolean) {
  return useQuery({
    ...recipesOptions(bookId, stage),
    select: (page) => page.items,
    enabled,
  });
}

/** What narrows the results a stage made on a page: one step of the stage. */
export interface VersionsFilter {
  /** The step whose results are read, or undefined for the results of every step. */
  step?: string;
}

/**
 * Query options of the full results a stage made on one page, earliest first.
 *
 * @param bookId The book.
 * @param pageId The page.
 * @param stage The stage.
 * @param filter The step to read, or nothing for the results of every step.
 */
export function versionsOptions(
  bookId: string,
  pageId: string,
  stage: Stage,
  filter: VersionsFilter = {},
) {
  const query = {
    stage,
    scale: 'full',
    size: LIST_SIZE,
    ...(filter.step === undefined ? {} : { step: filter.step }),
  } as const;
  return queryOptions({
    queryKey: listVersionsApiV1BooksBookIdPagesPageIdVersionsGetQueryKey({
      path: { book_id: bookId, page_id: pageId },
      query,
    }),
    queryFn: async ({ signal }): Promise<PageVersionSchema[]> => {
      const versions: PageVersionSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listVersionsApiV1BooksBookIdPagesPageIdVersionsGet({
          path: { book_id: bookId, page_id: pageId },
          query: { ...query, page },
          signal,
          throwOnError: true,
        });
        versions.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return versions;
    },
  });
}

/** Read the results a stage made on a page, for the history of the page and the chain of its steps. */
export function useVersions(
  bookId: string,
  pageId: string | undefined,
  stage: Stage,
  filter: VersionsFilter = {},
): UseQueryResult<PageVersionSchema[]> {
  return useQuery({
    ...versionsOptions(bookId, pageId ?? '', stage, filter),
    enabled: pageId !== undefined,
  });
}

/** Save the steps of a recipe. */
export function useSaveRecipe(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putRecipeApiV1BooksBookIdStagesStageRecipesRecipeIdPutMutation(),
    onSettled: () => invalidateAllRecipes(queryClient, bookId),
  });
}

/**
 * Put the steps a stage starts with back into a recipe, which marks the pages it processed out of date. The steps are the
 * default profile of the account or the built-in template, chosen by the server.
 */
export function useResetRecipe(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...resetRecipeApiV1BooksBookIdStagesStageRecipesRecipeIdResetPostMutation(),
    onSettled: () => invalidateAllRecipes(queryClient, bookId),
  });
}

/** The key that marks the requests to run a stage of a book, so that every part of the screen can see one in flight. */
function runKey(bookId: string): readonly [string, string] {
  return ['run-stage', bookId];
}

/**
 * Run the stage over some pages in the background, which the activity of the book then follows.
 *
 * The mutation stays pending until the jobs of the book are read again, so the job it started is on the list by the time
 * it ends, and {@link useRunInFlight} covers the stretch from the press to that moment.
 */
export function useRunStage(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...runStageApiV1BooksBookIdStagesStageRunPostMutation(),
    mutationKey: runKey(bookId),
    onSettled: () => invalidateJobs(queryClient, bookId),
  });
}

/**
 * Tell whether a request to run a stage of the book was sent from anywhere on the screen and its job is not yet on the
 * list of active jobs.
 *
 * The server runs one job at a time, and the list of active jobs lags the press by the round trip of the request and of
 * the read that follows. A control that decides from that list alone stays open in that stretch, and what it sends there
 * is refused with a conflict.
 */
export function useRunInFlight(bookId: string): boolean {
  return useIsMutating({ mutationKey: runKey(bookId) }) > 0;
}

/**
 * Measure the book in the background: the line height and the page size written into the normalize step of the recipe.
 *
 * The job reads the crop of every page and writes the recipe when it ends, which the event of the job tells the screen, so
 * the recipe is read again then and the form shows the new numbers.
 */
export function useMeasureBook(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...measureBookApiV1BooksBookIdStagesGeometryMeasurePostMutation(),
    onSettled: () => invalidateJobs(queryClient, bookId),
  });
}

/**
 * Count what a collection would delete from the book now: the versions and the bytes of their files.
 *
 * The query is on only while the dialog that shows it is open, and a run or a mark, which changes the count, reads it
 * again through the `book-changed` event of the versions.
 *
 * @param bookId The book.
 * @param enabled Whether the question is being asked.
 */
export function useCollectionReport(bookId: string, enabled: boolean) {
  return useQuery({
    ...collectableVersionsApiV1BooksBookIdVersionsCollectableGetOptions({
      path: { book_id: bookId },
    }),
    enabled,
  });
}

/**
 * Delete the old results of the book in the background, with their files, which the activity of the book then follows.
 *
 * The job tells the screen when it ends, and the lists of results are read again then, so only the jobs are read again
 * here.
 */
export function useCollectVersions(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...collectVersionsApiV1BooksBookIdVersionsCollectPostMutation(),
    onSettled: () => invalidateJobs(queryClient, bookId),
  });
}

/**
 * Detect what pages show in the background, for the pages named or for those that have no type yet.
 *
 * The job writes the type into the pages when it ends, which the event of the job tells the screen, so the manifest is
 * read again then and the marks of the pages show what was found.
 */
export function useDetectContent(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...detectContentTypesApiV1BooksBookIdPagesContentTypesDetectPostMutation(),
    onSettled: () => invalidateJobs(queryClient, bookId),
  });
}

/** Preview the steps of the form on one page in the background; the picture arrives as an event. */
export function usePreviewStep(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...previewStepApiV1BooksBookIdStagesStagePreviewPostMutation(),
    onSettled: () => invalidateJobs(queryClient, bookId),
  });
}

/** Read the settings one stage holds for the steps of a page: the fields the page changes, by step. */
export function usePageSettings(
  bookId: string,
  pageId: string | undefined,
  stage: Stage,
  enabled = true,
) {
  return useQuery({
    ...listSettingsApiV1BooksBookIdPagesPageIdSettingsStageGetOptions({
      path: { book_id: bookId, page_id: pageId ?? '', stage },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled: enabled && pageId !== undefined,
  });
}

/**
 * Set the value pages use for one field of a step: the open page, the pages selected, the odd pages, the even pages or
 * a group. The stage of each page whose parameters change is marked out of date on the server.
 *
 * The changes of values share one mutation scope per book, so they reach the server in the order they were made and an
 * older value never lands over a newer one. The settings of every page are read again, since a value for the odd pages
 * reaches half the book, and the rows of the stages follow the `book-changed` event.
 */
export function useSetValue(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putValueApiV1BooksBookIdStagesStageStepsStepIdValuesNamePutMutation(),
    scope: { id: `page-settings:${bookId}` },
    onSettled: () => invalidatePageLayers(queryClient),
  });
}

/** Take a value of a field back from the pages it was set for, so they use the next by strength, which marks their stages out of date. */
export function useRemoveValue(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteValueApiV1BooksBookIdStagesStageStepsStepIdValuesNameDeleteMutation(),
    scope: { id: `page-settings:${bookId}` },
    onSettled: () => invalidatePageLayers(queryClient),
  });
}

/**
 * Carry the shape the open page has set by hand for a step over to other pages, as one batch of the history.
 *
 * It shares the mutation scope of the edits of the book, so it reaches the server after the edit of the source page that
 * was saved just before it. The settings, the edits and the histories of every page it reached are read again.
 *
 * What it did is handed over by the mutation itself and not by the call that sent it, so a reader who moves to another
 * page while it is on its way, which takes the menu that sent it away, still sees the result and its undo.
 *
 * @param bookId The book whose pages the shape is carried over.
 * @param stage The stage of the step.
 * @param onCarried Called with what the carry-over did.
 */
export function useCarryShape(bookId: string, onCarried: (result: CarryOverSchema) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    ...carryOverEditApiV1BooksBookIdPagesPageIdEditsStageStepIdCarryOverPostMutation(),
    scope: { id: `page-edits:${bookId}` },
    onSuccess: onCarried,
    onSettled: () => invalidatePageLayers(queryClient),
  });
}

/** Count the pages a mode of a run would take work from, which nothing is written for. */
export function useRunImpact() {
  return useMutation(runImpactApiV1BooksBookIdStagesStageRunImpactPostMutation());
}

/**
 * Count how many pages of a run have work of their own, which is read again each time it is asked for, since a setting
 * or an edit of a page changes it without the run changing.
 *
 * @param bookId The book.
 * @param stage The stage.
 * @param body The run that keeps the work, or null when nothing is asked, such as while the menu of the run is shut.
 */
export function useOwnWork(bookId: string, stage: Stage, body: StageRunBody | null) {
  return useQuery({
    queryKey: ['run-own-work', bookId, stage, body],
    queryFn: async ({ signal }): Promise<RunImpactSchema> => {
      const { data } = await runImpactApiV1BooksBookIdStagesStageRunImpactPost({
        path: { book_id: bookId, stage },
        body: body ?? {},
        signal,
        throwOnError: true,
      });
      return data;
    },
    enabled: body !== null,
    staleTime: 0,
  });
}

/** Make one of the results of a page its current result in the stage, which the rows of the stage and its results show. */
export function useChooseVersion(bookId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...chooseVersionApiV1BooksBookIdPagesPageIdStagesStagePutMutation(),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        invalidateStageRows(queryClient, bookId, stage),
        invalidateVersions(queryClient, bookId, variables.path.page_id),
      ]),
  });
}

/**
 * Set the mark and the comment of a result, which are the user's notes and change nothing the step reads.
 *
 * Both are replaced together, so a caller that changes one sends the other as it is. The versions of the page are read
 * again, since every list of results shows the notes. The strip, which marks the pages whose result is marked bad,
 * follows the `book-changed` event.
 */
export function useMarkResult(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putMarkApiV1BooksBookIdPagesPageIdVersionsVersionIdMarkPutMutation(),
    onSettled: (_data, _error, variables) =>
      invalidateVersions(queryClient, bookId, variables.path.page_id),
  });
}

/**
 * Read every scan of the book as one list, which the Split stage needs for the size of each.
 *
 * The key extends the generated key of the list of scans, so the invalidation of the scans reaches it, and the extra
 * part keeps it apart from the one-page lists the Import stage reads.
 */
export function useAllScans(bookId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...listScansApiV1BooksBookIdScansGetQueryKey({ path: { book_id: bookId } }), 'all'],
    queryFn: async ({ signal }): Promise<ScanSchema[]> => {
      const scans: ScanSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listScansApiV1BooksBookIdScansGet({
          path: { book_id: bookId },
          query: { page, size: LIST_SIZE },
          signal,
          throwOnError: true,
        });
        scans.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return scans;
    },
    enabled,
  });
}
