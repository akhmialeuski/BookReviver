import { describe, expect, it } from 'vitest';
import type { StageSummarySchema } from '@/api';
import { stageProgress } from '@/features/workspace/progress';

function summary(counts: Partial<StageSummarySchema>): StageSummarySchema {
  return {
    stage: 'geometry',
    available: true,
    manual: false,
    pages: 0,
    fresh: 0,
    stale: 0,
    failed: 0,
    not_run: 0,
    review: 0,
    check: 0,
    active_recipe_id: null,
    ...counts,
  };
}

describe('stageProgress', () => {
  it('counts the pages that are done and the pages the stage goes over', () => {
    const progress = stageProgress(
      summary({ pages: 126, fresh: 108, stale: 8, failed: 2, not_run: 8 }),
    );
    expect(progress).toMatchObject({ done: 108, total: 126, failed: 2 });
  });

  it('shares the bar between the states in the order of the legend', () => {
    const { segments } = stageProgress(
      summary({ pages: 100, fresh: 50, stale: 25, failed: 5, not_run: 20 }),
    );
    expect(segments).toEqual([
      { status: 'fresh', percent: 50 },
      { status: 'stale', percent: 25 },
      { status: 'failed', percent: 5 },
      { status: 'not-run', percent: 20 },
    ]);
  });

  it('leaves out a state no page is in', () => {
    const { segments } = stageProgress(summary({ pages: 4, fresh: 4 }));
    expect(segments).toEqual([{ status: 'fresh', percent: 100 }]);
  });

  it('has no segments and no division by zero for a stage without pages', () => {
    const progress = stageProgress(summary({ pages: 0 }));
    expect(progress.segments).toEqual([]);
    expect(progress).toMatchObject({ done: 0, total: 0, check: 0, failed: 0 });
  });

  it('takes the pages to check from the server, as it counted them', () => {
    expect(
      stageProgress(summary({ pages: 126, fresh: 118, stale: 3, review: 5, check: 7 })).check,
    ).toBe(7);
  });

  it('does not add the out-of-date pages to the marked ones, since a page can be both', () => {
    const progress = stageProgress(
      summary({ pages: 10, fresh: 2, stale: 6, failed: 3, review: 6, check: 9 }),
    );
    expect(progress.check).toBe(9);
  });
});
