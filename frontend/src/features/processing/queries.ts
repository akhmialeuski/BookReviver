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
  type ScanSchema,
  type Stage,
} from '@/api';
import {
  activateVariantApiV1ProjectsProjectIdStagesStageVariantsRecipeIdActivatePostMutation,
  chooseVersionApiV1ProjectsProjectIdPagesPageIdStagesStagePutMutation,
  createRuleApiV1ProjectsProjectIdStagesStageRulesPostMutation,
  createVariantApiV1ProjectsProjectIdStagesStageVariantsPostMutation,
  deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyDeleteMutation,
  deleteRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdDeleteMutation,
  getRecipeApiV1ProjectsProjectIdStagesStageRecipeGetQueryKey,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetOptions,
  listEditsApiV1ProjectsProjectIdPagesPageIdEditsStageGetQueryKey,
  listProcessorsApiV1ProcessorsGetOptions,
  listRulesApiV1ProjectsProjectIdStagesStageRulesGetOptions,
  listRulesApiV1ProjectsProjectIdStagesStageRulesGetQueryKey,
  listScansApiV1ProjectsProjectIdScansGetQueryKey,
  listVariantsApiV1ProjectsProjectIdStagesStageVariantsGetOptions,
  listVariantsApiV1ProjectsProjectIdStagesStageVariantsGetQueryKey,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey,
  measureBookApiV1ProjectsProjectIdStagesGeometryMeasurePostMutation,
  previewStepApiV1ProjectsProjectIdStagesStagePreviewPostMutation,
  putEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyPutMutation,
  putRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdPutMutation,
  putVariantApiV1ProjectsProjectIdStagesStageVariantsRecipeIdPutMutation,
  runStageApiV1ProjectsProjectIdStagesStageRunPostMutation,
  unpinStageApiV1ProjectsProjectIdPagesPageIdStagesStagePinDeleteMutation,
} from '@/api/@tanstack/react-query.gen';
import {
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
 * always saved through the route of its id, which serves the active recipe as well as a variant, so one change covers
 * both. A save marks the pages it processed out of date on the server, and the rows and the summary of the stage are
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

/** Read the recipes of a stage, the active one first and then the variants. */
export function useRecipes(projectId: string, stage: Stage, enabled: boolean) {
  return useQuery({
    ...listVariantsApiV1ProjectsProjectIdStagesStageVariantsGetOptions({
      path: { project_id: projectId, stage },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled,
  });
}

/** Query options of the full results a stage made on one page, earliest first. */
export function versionsOptions(projectId: string, pageId: string, stage: Stage) {
  const query = { stage, scale: 'full', size: LIST_SIZE } as const;
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

/** Read the results a stage made on a page, for the history of the page. */
export function useVersions(
  projectId: string,
  pageId: string | undefined,
  stage: Stage,
): UseQueryResult<PageVersionSchema[]> {
  return useQuery({
    ...versionsOptions(projectId, pageId ?? '', stage),
    enabled: pageId !== undefined,
  });
}

/** Read the rules of a stage in the order they are tried, which send pages to its variants. */
export function useRules(projectId: string, stage: Stage, enabled: boolean) {
  return useQuery({
    ...listRulesApiV1ProjectsProjectIdStagesStageRulesGetOptions({
      path: { project_id: projectId, stage },
      query: { size: LIST_SIZE },
    }),
    select: (page) => page.items,
    enabled,
  });
}

/** Read the rules of a stage again, after one was added, moved to another variant or removed. */
function refreshRules(queryClient: QueryClient, projectId: string, stage: Stage): Promise<void> {
  return queryClient.invalidateQueries({
    queryKey: listRulesApiV1ProjectsProjectIdStagesStageRulesGetQueryKey({
      path: { project_id: projectId, stage },
    }),
  });
}

/** Add a rule that sends the pages meeting a condition to a variant. */
export function useCreateRule(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...createRuleApiV1ProjectsProjectIdStagesStageRulesPostMutation(),
    onSettled: () => refreshRules(queryClient, projectId, stage),
  });
}

/** Send the pages a rule matches to another variant. */
export function useRetargetRule(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdPutMutation(),
    onSettled: () => refreshRules(queryClient, projectId, stage),
  });
}

/** Remove a rule, so the pages it matched fall to the rules after it or to the active recipe. */
export function useDeleteRule(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteRuleApiV1ProjectsProjectIdStagesStageRulesRuleIdDeleteMutation(),
    onSettled: () => refreshRules(queryClient, projectId, stage),
  });
}

/** Mark stale what a change of the recipes or of the results of a stage changes. */
async function refreshStage(
  queryClient: QueryClient,
  projectId: string,
  stage: Stage,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: listVariantsApiV1ProjectsProjectIdStagesStageVariantsGetQueryKey({
        path: { project_id: projectId, stage },
      }),
    }),
    queryClient.invalidateQueries({
      queryKey: getRecipeApiV1ProjectsProjectIdStagesStageRecipeGetQueryKey({
        path: { project_id: projectId, stage },
      }),
    }),
    invalidateStageRows(queryClient, projectId, stage),
    invalidateStageSummary(queryClient, projectId),
    invalidateProject(queryClient, projectId),
    invalidateJobs(queryClient, projectId),
  ]);
}

/** Save the name and the steps of a recipe, the active one or a variant. */
export function useSaveRecipe(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putVariantApiV1ProjectsProjectIdStagesStageVariantsRecipeIdPutMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}

/** Add a variant of the stage, which is not active until it is activated. */
export function useCreateVariant(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...createVariantApiV1ProjectsProjectIdStagesStageVariantsPostMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}

/** Make a variant the recipe the stage runs by, which marks the pages the old one processed out of date. */
export function useActivateRecipe(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...activateVariantApiV1ProjectsProjectIdStagesStageVariantsRecipeIdActivatePostMutation(),
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

/** Save the edit a processor reads on a page, which marks the stage of the page out of date. */
export function useSaveEdit(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...putEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyPutMutation(),
    onSettled: (_data, _error, variables) =>
      refreshEdits(queryClient, projectId, stage, variables.path.page_id),
  });
}

/** Delete the edit a processor reads on a page, which marks the stage of the page out of date. */
export function useDeleteEdit(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...deleteEditApiV1ProjectsProjectIdPagesPageIdEditsStageProcessorKeyDeleteMutation(),
    onSettled: (_data, _error, variables) =>
      refreshEdits(queryClient, projectId, stage, variables.path.page_id),
  });
}

/** Take the pinned variant off a page, so a run of the stage chooses its variant by the rules again. */
export function useUnpin(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...unpinStageApiV1ProjectsProjectIdPagesPageIdStagesStagePinDeleteMutation(),
    onSettled: () => refreshStage(queryClient, projectId, stage),
  });
}

/** Make one of the results of a page its current result in the stage. */
export function useChooseVersion(projectId: string, stage: Stage) {
  const queryClient = useQueryClient();
  return useMutation({
    ...chooseVersionApiV1ProjectsProjectIdPagesPageIdStagesStagePutMutation(),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        refreshStage(queryClient, projectId, stage),
        queryClient.invalidateQueries({
          queryKey: listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey({
            path: { project_id: projectId, page_id: variables.path.page_id },
          }),
        }),
      ]),
  });
}

/**
 * Read every scan of the book as one list, which the Split stage needs for the size of each.
 *
 * The key extends the generated key of the list of scans, so the invalidation of the scans reaches it, and the extra
 * part keeps it apart from the one-page lists the Import stage reads.
 */
export function useAllScans(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: [
      ...listScansApiV1ProjectsProjectIdScansGetQueryKey({ path: { project_id: projectId } }),
      'all',
    ],
    queryFn: async ({ signal }): Promise<ScanSchema[]> => {
      const scans: ScanSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listScansApiV1ProjectsProjectIdScansGet({
          path: { project_id: projectId },
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
