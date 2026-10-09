import { queryOptions, type UseQueryResult, useQuery } from '@tanstack/react-query';
import {
  type JobSchema,
  type ListStagesApiV1ProjectsProjectIdStagesGetError,
  listProjectJobsApiV1ProjectsProjectIdJobsGet,
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet,
  type Stage,
  type StagePageSchema,
  type StageSummarySchema,
} from '@/api';
import {
  listProjectJobsApiV1ProjectsProjectIdJobsGetQueryKey,
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey,
  listStagesApiV1ProjectsProjectIdStagesGetOptions,
} from '@/api/@tanstack/react-query.gen';

/**
 * The queries behind the workspace: the summary of the stages, the rows of one stage, and the jobs of the book.
 *
 * Their keys are the generated ones, so the invalidations of `features/projects/queries.ts` that the event stream
 * calls reach them. The rows are read like the manifest, whole and as one list, since the strip is virtual and the
 * book of a thousand pages fits in one request.
 */

/** Rows asked for per request; the route accepts at most this many. */
export const ROWS_PAGE_SIZE = 1000;

/** How many jobs the list of the activity shows, the newest first. */
export const JOBS_LISTED = 20;

/**
 * Query options of the whole manifest of rows of one stage.
 *
 * @param projectId The book.
 * @param stage The stage.
 * @param step A step of the recipe to place every page at, or undefined for the rows of the stage alone.
 */
export function stageRowsOptions(projectId: string, stage: Stage, step?: string) {
  const extra = step === undefined ? {} : { step };
  return queryOptions({
    queryKey: listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey({
      path: { project_id: projectId, stage },
      query: { size: ROWS_PAGE_SIZE, ...extra },
    }),
    queryFn: async ({ signal }): Promise<StagePageSchema[]> => {
      const rows: StagePageSchema[] = [];
      let page = 1;
      let total = 1;
      while (page <= total) {
        const { data } = await listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet({
          path: { project_id: projectId, stage },
          query: { page, size: ROWS_PAGE_SIZE, ...extra },
          signal,
          throwOnError: true,
        });
        rows.push(...data.items);
        total = data.pages;
        page += 1;
      }
      return rows;
    },
  });
}

/** Read where every page of the book stands in one stage. */
export function useStageRows(projectId: string, stage: Stage): UseQueryResult<StagePageSchema[]> {
  return useQuery(stageRowsOptions(projectId, stage));
}

/**
 * Read where every page of the book stands at one step of a stage; nothing is read while no step is open.
 *
 * The rows of a step change with every run of a step before it, and the rows of a step nobody looks at are only marked
 * stale by those runs, not read again. Kept in the cache, they would be shown as current when the step opens again, and
 * an editor would start from a step that had not run yet. So they are dropped as soon as their step closes, and a step
 * that opens again has no rows until the server answers.
 */
export function useStepRows(
  projectId: string,
  stage: Stage,
  step: string | undefined,
): UseQueryResult<StagePageSchema[]> {
  return useQuery({
    ...stageRowsOptions(projectId, stage, step),
    enabled: step !== undefined,
    gcTime: 0,
  });
}

/** Read the summary of every stage of the book, in the order of the pipeline. */
export function useStageSummaries(
  projectId: string,
): UseQueryResult<StageSummarySchema[], ListStagesApiV1ProjectsProjectIdStagesGetError> {
  return useQuery({
    ...listStagesApiV1ProjectsProjectIdStagesGetOptions({ path: { project_id: projectId } }),
    select: (page) => page.items,
  });
}

/**
 * Query options of the jobs of the book.
 *
 * @param projectId The book.
 * @param active Whether to list only the jobs that are queued or running, as the chip does, or the latest of any state.
 */
export function jobsOptions(projectId: string, active: boolean) {
  const query = active ? { active: true, size: JOBS_LISTED } : { size: JOBS_LISTED };
  return queryOptions({
    queryKey: listProjectJobsApiV1ProjectsProjectIdJobsGetQueryKey({
      path: { project_id: projectId },
      query,
    }),
    queryFn: async ({ signal }): Promise<JobSchema[]> => {
      const { data } = await listProjectJobsApiV1ProjectsProjectIdJobsGet({
        path: { project_id: projectId },
        query,
        signal,
        throwOnError: true,
      });
      return data.items;
    },
  });
}

/** Read the jobs of the book that are queued or running. */
export function useActiveJobs(projectId: string): UseQueryResult<JobSchema[]> {
  return useQuery(jobsOptions(projectId, true));
}

/** Read the latest jobs of the book in every state, for the list of the activity when it is open. */
export function useRecentJobs(projectId: string, enabled: boolean): UseQueryResult<JobSchema[]> {
  return useQuery({ ...jobsOptions(projectId, false), enabled });
}

/**
 * Query options of one row of a stage at one step: the page at a place in the book, which a request of a single row
 * reads, so the step bar can tell the state of every step on the open page without reading the whole book for each.
 *
 * @param projectId The book.
 * @param stage The stage.
 * @param step The step of the recipe to place the page at.
 * @param position Place of the page in the book from zero.
 */
export function stepRowOptions(projectId: string, stage: Stage, step: string, position: number) {
  const query = { step, page: position + 1, size: 1 };
  return queryOptions({
    queryKey: listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey({
      path: { project_id: projectId, stage },
      query,
    }),
    queryFn: async ({ signal }): Promise<StagePageSchema | null> => {
      const { data } = await listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet({
        path: { project_id: projectId, stage },
        query,
        signal,
        throwOnError: true,
      });
      return data.items[0] ?? null;
    },
  });
}
