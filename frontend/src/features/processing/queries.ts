import {
  type QueryClient,
  queryOptions,
  type UseQueryResult,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import {
  listScansApiV1ProjectsProjectIdScansGet,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet,
  type PageVersionSchema,
  type ResultMark,
  type RunImpactSchema,
  runImpactApiV1ProjectsProjectIdStagesStageRunImpactPost,
  type ScanSchema,
  type Stage,
  type StageRunBody,
} from '@/api';
import {
  carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPostMutation,
  chooseVersionApiV1ProjectsProjectIdPagesPageIdStagesStagePutMutation,
  deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdDeleteMutation,
  deleteValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNameDeleteMutation,
  detectContentTypesApiV1ProjectsProjectIdPagesContentTypesDetectPostMutation,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetOptions,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetQueryKey,
  listProcessorsApiV1ProcessorsGetOptions,
  listRecipesApiV1ProjectsProjectIdStagesStageRecipesGetOptions,
  listRecipesApiV1ProjectsProjectIdStagesStageRecipesGetQueryKey,
  listScansApiV1ProjectsProjectIdScansGetQueryKey,
  listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGetOptions,
  listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGetQueryKey,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey,
  measureBookApiV1ProjectsProjectIdStagesGeometryMeasurePostMutation,
  previewStepApiV1ProjectsProjectIdStagesStagePreviewPostMutation,
  putEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdPutMutation,
  putMarkApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdMarkPutMutation,
  putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPutMutation,
  putValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNamePutMutation,
  remakeVersionApiV1ProjectsProjectIdPagesPageIdVersionsVersionIdRemakePostMutation,
  resetRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdResetPostMutation,
  runImpactApiV1ProjectsProjectIdStagesStageRunImpactPostMutation,
  runStageApiV1ProjectsProjectIdStagesStageRunPostMutation,
} from '@/api/@tanstack/react-query.gen';
import { invalidateHistory, invalidatePageLayers } from '@/features/processing/historyQueries';
import {
  invalidateAllStageRows,
  invalidateJobs,
  invalidateProject,
  invalidateStageRows,
  invalidateStageSummary,
} from '@/features/projects/queries';

/**
 * The queries and changes behind the processing workspace: the catalogue of processors, the recipes of a stage, the
 * versions of a page, and the saving, running, previewing and choosing that act on them.
 *
 * The keys are the generated ones, so a change of a recipe marks exactly the reads of that stage stale. A recipe is
 * always saved through the route of its id. A save marks the pages it processed out of date on the server, and the rows and the summary of the stage are
 * read again at once, since the counts on the panel and the bar depend on them.
 */

/** Items asked for per request of a short list; the routes accept at most this many. */
const LIST_SIZE = 100;

/** Read the catalogue of processors, which a stage is built from. */
export function useProcessors() {
  return useQuery({
    ...listProcessorsApiV1ProcessorsGetOptions({ query: { size: LIST_SIZE } }),
    select: (page) => page.items,
    // The installed plugins change with a restart of the server, not while a screen is open
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Read the recipes of a stage, one for each kind of page, in the order of the kinds. */
export function useRecipes(projectId: string, stage: Stage, enabled: boolean) {
  return useQuery({
    ...listRecipesApiV1ProjectsProjectIdStagesStageRecipesGetOptions({
      path: { project_id: projectId, stage },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled,
  });
}

/** What narrows the results a stage made on a page: one step of the stage, and one mark. */
export interface VersionsFilter {
  /** The step whose results are read, or undefined for the results of every step. */
  step?: string;
  /** The mark the results carry, or undefined for every result. */
  mark?: ResultMark;
}

/**
 * Query options of the full results a stage made on one page, earliest first.
 *
 * @param projectId The book.
 * @param pageId The page.
 * @param stage The stage.
 * @param filter The step and the mark to read, or nothing for the results of every step.
 */
export function versionsOptions(
  projectId: string,
  pageId: string,
  stage: Stage,
  filter: VersionsFilter = {},
) {
  const query = {
    stage,
    scale: 'full',
    size: LIST_SIZE,
    ...(filter.step === undefined ? {} : { step: filter.step }),
    ...(filter.mark === undefined ? {} : { mark: filter.mark }),
  } as const;
  return queryOptions({
    queryKey: listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey({
      path: { project_id: projectId, page_id: pageId },
      query,
    }),
    queryFn: async ({ signal }): Promise<PageVersionSchema[]> => {
      const versions: PageVersionSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet({
          path: { project_id: projectId, page_id: pageId },
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
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
  filter: VersionsFilter = {},
): UseQueryResult<PageVersionSchema[]> {
  return useQuery({
    ...versionsOptions(projectId, pageId ?? '', stage, filter),
    enabled: pageId !== undefined,
  });
}

/** Mark stale what a change of the recipes or of the results of a stage changes. */
export async function refreshStage(
  queryClient: QueryClient,
  projectId: string,
  stage: Stage,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: listRecipesApiV1ProjectsProjectIdStagesStageRecipesGetQueryKey({
        path: { project_id: projectId, stage },
      }),
    }),
    invalidateStageRows(queryClient, projectId, stage),
    invalidateStageSummary(queryClient, projectId),
    invalidateProject(queryClient, projectId),
    invalidateJobs(queryClient, projectId),
  ]);
}

/** Save the steps of a recipe. */
export function useSaveRecipe(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdPutMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}

/**
 * Put the steps a stage starts with back into a recipe, which marks the pages it processed out of date. The steps are the
 * default profile of the account or the built-in template, chosen by the server.
 */
export function useResetRecipe(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...resetRecipeApiV1ProjectsProjectIdStagesStageRecipesRecipeIdResetPostMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}

/** The key that marks the requests to run a stage of a book, so that every part of the screen can see one in flight. */
function runKey(projectId: string): readonly [string, string] {
  return ['run-stage', projectId];
}

/**
 * Run the stage over some pages in the background, which the activity of the book then follows.
 *
 * The mutation stays pending until the jobs of the book are read again, so the job it started is on the list by the time
 * it ends, and {@link useRunInFlight} covers the stretch from the press to that moment.
 */
export function useRunStage(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...runStageApiV1ProjectsProjectIdStagesStageRunPostMutation(),
    mutationKey: runKey(projectId),
    onSettled: () => invalidateJobs(queryClient, projectId),
    onSuccess: () => refreshStage(queryClient, projectId, stage),
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
export function useRunInFlight(projectId: string): boolean {
  return useIsMutating({ mutationKey: runKey(projectId) }) > 0;
}

/**
 * Measure the book in the background: the line height and the page size written into the normalize step of the recipe.
 *
 * The job reads the crop of every page and writes the recipe when it ends, which the event of the job tells the screen, so
 * the recipe is read again then and the form shows the new numbers.
 */
export function useMeasureBook(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...measureBookApiV1ProjectsProjectIdStagesGeometryMeasurePostMutation(),
    onSettled: () => invalidateJobs(queryClient, projectId),
  });
}

/**
 * Detect what pages show in the background, for the pages named or for those that have no type yet.
 *
 * The job writes the type into the pages when it ends, which the event of the job tells the screen, so the manifest is
 * read again then and the marks of the pages show what was found.
 */
export function useDetectContent(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...detectContentTypesApiV1ProjectsProjectIdPagesContentTypesDetectPostMutation(),
    onSettled: () => invalidateJobs(queryClient, projectId),
  });
}

/** Preview the steps of the form on one page in the background; the picture arrives as an event. */
export function usePreviewStep(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    ...previewStepApiV1ProjectsProjectIdStagesStagePreviewPostMutation(),
    onSettled: () => invalidateJobs(queryClient, projectId),
  });
}

/** Read the manual edits one stage holds on a page, by processor. */
export function useEdits(projectId: string, pageId: string | undefined, stage: Stage) {
  return useQuery({
    ...listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetOptions({
      path: { project_id: projectId, page_id: pageId ?? '', stage },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled: pageId !== undefined,
  });
}

/** Mark stale what a change of an edit of a page changes: its edits, and the rows and the summary of the stage. */
async function refreshEdits(
  queryClient: QueryClient,
  projectId: string,
  stage: Stage,
  pageId: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetQueryKey({
        path: { project_id: projectId, page_id: pageId, stage },
      }),
    }),
    invalidateStageRows(queryClient, projectId, stage),
    invalidateStageSummary(queryClient, projectId),
  ]);
}

/** Save the edit a step of a recipe reads on a page, which marks the stage of the page out of date. */
export function useSaveEdit(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdPutMutation(),
    onSettled: (_data, _error, variables) =>
      refreshEdits(queryClient, projectId, stage, variables.path.page_id),
  });
}

/** Delete the edit a step of a recipe reads on a page, which marks the stage of the page out of date. */
export function useDeleteEdit(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdDeleteMutation(),
    onSettled: (_data, _error, variables) =>
      refreshEdits(queryClient, projectId, stage, variables.path.page_id),
  });
}

/** Read the settings one stage holds for the steps of a page: the fields the page changes, by step. */
export function usePageSettings(
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
  enabled = true,
) {
  return useQuery({
    ...listSettingsApiV1ProjectsProjectIdPagesPageIdSettingsStageGetOptions({
      path: { project_id: projectId, page_id: pageId ?? '', stage },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled: enabled && pageId !== undefined,
  });
}

/** Mark stale what a change of a value of a setting changes: the settings of every page, and the rows and the summary of the stage. */
async function refreshValues(queryClient: QueryClient, projectId: string, stage: Stage): Promise<void> {
  await Promise.all([
    invalidatePageLayers(queryClient),
    invalidateStageRows(queryClient, projectId, stage),
    invalidateStageSummary(queryClient, projectId),
  ]);
}

/**
 * Set the value pages use for one field of a step: the open page, the pages selected, the odd pages, the even pages or
 * a group. The stage of each page whose parameters change is marked out of date on the server.
 *
 * The changes of values share one mutation scope per book, so they reach the server in the order they were made and an
 * older value never lands over a newer one. The settings of every page are read again, since a value for the odd pages
 * reaches half the book.
 */
export function useSetValue(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNamePutMutation(),
    scope: { id: `page-settings:${projectId}` },
    onSettled: () => refreshValues(queryClient, projectId, stage),
  });
}

/** Take a value of a field back from the pages it was set for, so they use the next by strength, which marks their stages out of date. */
export function useRemoveValue(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteValueApiV1ProjectsProjectIdStagesStageStepsStepIdValuesNameDeleteMutation(),
    scope: { id: `page-settings:${projectId}` },
    onSettled: () => refreshValues(queryClient, projectId, stage),
  });
}

/**
 * Carry the shape the open page has set by hand for a step over to other pages, as one batch of the history.
 *
 * It shares the mutation scope of the edits of the book, so it reaches the server after the edit of the source page that
 * was saved just before it. The edits and the histories of every page it reached are read again.
 */
export function useCarryShape(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...carryOverEditApiV1ProjectsProjectIdPagesPageIdEditsStageStepIdCarryOverPostMutation(),
    scope: { id: `page-edits:${projectId}` },
    onSettled: () =>
      Promise.all([
        invalidatePageLayers(queryClient),
        invalidateStageRows(queryClient, projectId, stage),
        invalidateStageSummary(queryClient, projectId),
      ]),
  });
}

/** Count the pages a mode of a run would take work from, which nothing is written for. */
export function useRunImpact() {
  return useMutation(runImpactApiV1ProjectsProjectIdStagesStageRunImpactPostMutation());
}

/**
 * Count how many pages of a run have work of their own, which is read again each time it is asked for, since a setting
 * or an edit of a page changes it without the run changing.
 *
 * @param projectId The book.
 * @param stage The stage.
 * @param body The run that keeps the work, or null when nothing is asked, such as while the menu of the run is shut.
 */
export function useOwnWork(projectId: string, stage: Stage, body: StageRunBody | null) {
  return useQuery({
    queryKey: ['run-own-work', projectId, stage, body],
    queryFn: async ({ signal }): Promise<RunImpactSchema> => {
      const { data } = await runImpactApiV1ProjectsProjectIdStagesStageRunImpactPost({
        path: { project_id: projectId, stage },
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
