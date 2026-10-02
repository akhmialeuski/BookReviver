import { type QueryObserverResult, useQueries } from '@tanstack/react-query';
import { useMemo } from 'react';
import type { Stage, StagePageSchema } from '@/api';
import { STAGES } from '@/features/stages/stages';
import { stageRowsOptions, useStageSummaries } from '@/features/workspace/queries';

/**
 * Reads the rows of the stages that come before a stage and make pictures, which the picture before a compare is taken
 * from.
 *
 * A stage that is done by hand, or that has no processor, makes no picture of its own, so it is not read. The rows are
 * given nearest stage first, each by the identifier of the page, so the picture before a page is the first one found.
 */

function byPage(results: readonly QueryObserverResult<StagePageSchema[]>[]) {
  return results.map(
    (result): ReadonlyMap<string, StagePageSchema> =>
      new Map((result.data ?? []).map((row) => [row.page_id, row])),
  );
}

/**
 * Read the rows of the earlier stages that process pages.
 *
 * @param projectId The book.
 * @param stage The stage that is open.
 * @param enabled Whether to read at all, which a stage with no compare does not need.
 */
export function useEarlierRows(
  projectId: string,
  stage: Stage,
  enabled: boolean,
): ReadonlyArray<ReadonlyMap<string, StagePageSchema>> {
  const summaries = useStageSummaries(projectId);
  const earlier = useMemo(() => {
    const index = STAGES.findIndex((entry) => entry.stage === stage);
    return STAGES.slice(0, Math.max(index, 0))
      .map((entry) => entry.stage)
      .filter((candidate) => {
        const summary = summaries.data?.find((entry) => entry.stage === candidate);
        return summary?.available === true && !summary.manual;
      })
      .reverse();
  }, [stage, summaries.data]);

  return useQueries({
    queries: earlier.map((candidate) => ({
      ...stageRowsOptions(projectId, candidate),
      enabled,
    })),
    combine: byPage,
  });
}
