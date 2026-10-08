import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSchema, Stage } from '@/api';
import {
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey,
  readJobApiV1JobsJobIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { pagesScope, versionReadyKey } from '@/features/projects/queries';
import { STAGES } from '@/features/stages/stages';
import { applyProjectEvent, BURST_DELAY_MS, EventName, isActiveJob, isJob } from './events';

/** The endpoint of the rows of a stage, once for each of the ten stages, as the invalidations list it. */
const STAGE_ROWS = STAGES.map(() => 'listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet');

/**
 * How events change the cache: a job event is stored as it is, the others mark the affected lists stale.
 */

const PROJECT_ID = 'p-1';

function job(state: JobSchema['state'], done = 1, total = 4): JobSchema {
  return {
    id: 'j-1',
    project_id: PROJECT_ID,
    kind: 'import-source',
    stage: null,
    state,
    progress: { done, total, fraction: total === 0 ? 0 : done / total },
    error: '',
    result: null,
    created_at: '2026-01-01T00:00:00Z',
    started_at: null,
    finished_at: null,
  };
}

/** Start recording the endpoints whose queries are invalidated; the returned function lists them. */
function watchInvalidations(queryClient: QueryClient): () => string[] {
  const spy = vi.spyOn(queryClient, 'invalidateQueries');
  return () =>
    spy.mock.calls.map(([filters]) => {
      const key = filters?.queryKey;
      const head = Array.isArray(key) ? key[0] : undefined;
      return typeof head === 'object' && head !== null && '_id' in head ? String(head._id) : '';
    });
}

describe('applyProjectEvent', () => {
  let queryClient: QueryClient;
  let invalidated: () => string[];

  beforeEach(() => {
    queryClient = new QueryClient();
    invalidated = watchInvalidations(queryClient);
  });

  it('leaves the pages alone on a pages-changed event while a change of the reader is in flight', () => {
    // The change that is in flight reads the pages again when it ends, and by then sees what this event announces
    void queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationFn: () => new Promise(() => undefined),
        scope: pagesScope(PROJECT_ID),
      })
      .execute(undefined);

    applyProjectEvent(queryClient, PROJECT_ID, { event: EventName.PagesChanged, data: {} });

    expect(invalidated()).not.toContain('listPagesApiV1ProjectsProjectIdPagesGet');
    expect(invalidated()).toContain('projectApiV1ProjectsProjectIdGet');
  });

  it('does not hold back the pages for a change of another book', () => {
    void queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationFn: () => new Promise(() => undefined),
        scope: pagesScope('p-2'),
      })
      .execute(undefined);

    applyProjectEvent(queryClient, PROJECT_ID, { event: EventName.PagesChanged, data: {} });

    expect(invalidated()).toContain('listPagesApiV1ProjectsProjectIdPagesGet');
  });

  it('writes the job of a running job-changed event into the query of that job', () => {
    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.JobChanged,
      data: job('running', 2),
    });

    const cached = queryClient.getQueryData(
      readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: 'j-1' } }),
    );
    expect(cached).toMatchObject({ state: 'running', progress: { done: 2 } });
    expect(invalidated()).toEqual([]);
  });

  it('refreshes the book, its sources, scans and the list when a job finishes', () => {
    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.JobChanged,
      data: job('succeeded', 4),
    });

    expect(invalidated().sort()).toEqual(
      [
        'listPagesApiV1ProjectsProjectIdPagesGet',
        'listProjectJobsApiV1ProjectsProjectIdJobsGet',
        'listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGet',
        'listProjectsApiV1ProjectsGet',
        'listScansApiV1ProjectsProjectIdScansGet',
        'listSourcesApiV1ProjectsProjectIdSourcesGet',
        'listStagesApiV1ProjectsProjectIdStagesGet',
        ...STAGE_ROWS,
        'projectApiV1ProjectsProjectIdGet',
      ].sort(),
    );
  });

  it('reads the recipes of the Geometry stage again when the measure of the book finishes', () => {
    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.JobChanged,
      data: { ...job('succeeded', 3, 3), kind: 'measure-book' },
    });

    expect(invalidated()).toContain('listRecipesApiV1ProjectsProjectIdStagesStageRecipesGet');
  });

  it('leaves the recipes alone while the measure runs, and when another job finishes', () => {
    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.JobChanged,
      data: { ...job('running', 1, 3), kind: 'measure-book' },
    });
    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.JobChanged,
      data: job('succeeded', 4),
    });
  });

  it('marks the results of every page of the book stale when the collection of old versions finishes', () => {
    const resultsOf = (projectId: string, pageId: string) =>
      listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey({
        path: { project_id: projectId, page_id: pageId },
        query: { stage: 'geometry' },
      });
    for (const key of [
      resultsOf(PROJECT_ID, 'pg-1'),
      resultsOf(PROJECT_ID, 'pg-2'),
      resultsOf('p-2', 'pg-3'),
    ]) {
      queryClient.setQueryData(key, []);
    }

    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.JobChanged,
      data: { ...job('succeeded', 2, 2), kind: 'collect-versions' },
    });

    // A result the job deleted is no longer offered, so the history of every page of the book is read again
    const stale = (key: ReturnType<typeof resultsOf>) =>
      queryClient.getQueryState(key)?.isInvalidated;
    expect(stale(resultsOf(PROJECT_ID, 'pg-1'))).toBe(true);
    expect(stale(resultsOf(PROJECT_ID, 'pg-2'))).toBe(true);
    expect(stale(resultsOf('p-2', 'pg-3'))).toBe(false);
  });

  it('keeps the version a page-version-ready event names and marks the results of that page stale', () => {
    applyProjectEvent(queryClient, PROJECT_ID, {
      event: EventName.PageVersionReady,
      data: { project_id: PROJECT_ID, page_id: 'pg-1', version_id: 'abc' },
    });

    expect(queryClient.getQueryData(versionReadyKey(PROJECT_ID, 'pg-1'))).toMatchObject({
      versionId: 'abc',
    });
    expect(invalidated().sort()).toEqual([
      'listPagesApiV1ProjectsProjectIdPagesGet',
      'listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet',
    ]);
  });

  it('shows a version announced twice as a new event', () => {
    const event = {
      event: EventName.PageVersionReady,
      data: { project_id: PROJECT_ID, page_id: 'pg-1', version_id: 'abc' },
    };
    vi.useFakeTimers();
    vi.setSystemTime(1000);
    applyProjectEvent(queryClient, PROJECT_ID, event);
    vi.setSystemTime(2000);
    applyProjectEvent(queryClient, PROJECT_ID, event);
    vi.useRealTimers();

    expect(queryClient.getQueryData(versionReadyKey(PROJECT_ID, 'pg-1'))).toEqual({
      versionId: 'abc',
      at: 2000,
    });
  });

  it('ignores a job-changed event whose data is not a job', () => {
    applyProjectEvent(queryClient, PROJECT_ID, { event: EventName.JobChanged, data: { id: 5 } });

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(invalidated()).toEqual([]);
  });

  it.each([
    [
      EventName.SourceImported,
      [
        'listPagesApiV1ProjectsProjectIdPagesGet',
        'listProjectsApiV1ProjectsGet',
        'listSourcesApiV1ProjectsProjectIdSourcesGet',
        'projectApiV1ProjectsProjectIdGet',
      ],
    ],
    [
      EventName.ScanReady,
      ['listPagesApiV1ProjectsProjectIdPagesGet', 'listScansApiV1ProjectsProjectIdScansGet'],
    ],
    [
      EventName.PagesChanged,
      [
        'listPagesApiV1ProjectsProjectIdPagesGet',
        'listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGet',
        'listProjectsApiV1ProjectsGet',
        'listStagesApiV1ProjectsProjectIdStagesGet',
        ...STAGE_ROWS,
        'projectApiV1ProjectsProjectIdGet',
      ].sort(),
    ],
    [
      EventName.ProjectChanged,
      ['listProjectsApiV1ProjectsGet', 'projectApiV1ProjectsProjectIdGet'],
    ],
    [EventName.PageVersionReady, ['listPagesApiV1ProjectsProjectIdPagesGet']],
  ])('on %s marks %j stale', (name, expected) => {
    applyProjectEvent(queryClient, PROJECT_ID, { event: name, data: {} });

    expect(invalidated().sort()).toEqual(expected);
  });
});

/** The bursts wait on timers and remember what is scheduled, so each test uses a book of its own. */
describe('applyProjectEvent for the events of a burst', () => {
  let queryClient: QueryClient;
  let invalidated: () => string[];

  beforeEach(() => {
    vi.useFakeTimers();
    queryClient = new QueryClient();
    invalidated = watchInvalidations(queryClient);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function stageChanged(book: string, stage: string, times = 1): void {
    for (let event = 0; event < times; event += 1) {
      applyProjectEvent(queryClient, book, {
        event: EventName.PageStageChanged,
        data: { project_id: book, page_id: `page-${event}`, stage },
      });
    }
  }

  it('marks the summary, the rows of that stage, the book and the list stale once for a burst of page events', () => {
    // The last stage has no later one, so its rows are the only rows the burst marks
    stageChanged('burst-1', 'typesetting', 50);
    expect(invalidated()).toEqual([]);

    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated().sort()).toEqual([
      'listProjectsApiV1ProjectsGet',
      'listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet',
      'listStagesApiV1ProjectsProjectIdStagesGet',
      'projectApiV1ProjectsProjectIdGet',
    ]);
  });

  it('reads the rows of the stage that changed and of the stages after it, which draw what it made', () => {
    stageChanged('burst-2', 'geometry');
    vi.advanceTimersByTime(BURST_DELAY_MS);

    const stages = vi.mocked(queryClient.invalidateQueries).mock.calls.flatMap(([filters]) => {
      const head = filters?.queryKey?.[0];
      return typeof head === 'object' &&
        head !== null &&
        'path' in head &&
        typeof head.path === 'object' &&
        head.path !== null &&
        'stage' in head.path
        ? [head.path.stage]
        : [];
    });
    expect(stages).toEqual([
      'geometry',
      'cleanup',
      'layout',
      'background',
      'recognition',
      'proofreading',
      'typesetting',
    ]);
  });

  it('marks the cached rows of a later stage stale when a page changes in an earlier one', () => {
    const rowsKey = (stage: Stage) =>
      listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey({
        path: { project_id: 'burst-2b', stage },
      });
    const keys = { earlier: rowsKey('page-order'), later: rowsKey('cleanup') };
    queryClient.setQueryData(keys.earlier, []);
    queryClient.setQueryData(keys.later, []);

    stageChanged('burst-2b', 'geometry');
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(queryClient.getQueryState(keys.later)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(keys.earlier)?.isInvalidated).toBe(false);
  });

  it('keeps the bursts of two stages apart', () => {
    stageChanged('burst-3', 'proofreading', 3);
    stageChanged('burst-3', 'typesetting', 3);
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(
      invalidated().filter(
        (id) => id === 'listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet',
      ),
    ).toHaveLength(3);
  });

  it('ignores a page-stage-changed event that names no stage', () => {
    stageChanged('burst-4', 'ocr');
    applyProjectEvent(queryClient, 'burst-4', { event: EventName.PageStageChanged, data: 'text' });
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toEqual([]);
  });

  it('marks the jobs stale once for a burst of progress events of a running job', () => {
    for (let done = 1; done <= 20; done += 1) {
      applyProjectEvent(queryClient, 'burst-5', {
        event: EventName.JobChanged,
        data: job('running', done, 20),
      });
    }
    expect(invalidated()).toEqual([]);

    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toEqual(['listProjectJobsApiV1ProjectsProjectIdJobsGet']);
  });
});

describe('isJob', () => {
  it('accepts the shape of a job and refuses other data', () => {
    expect(isJob(job('queued'))).toBe(true);
    expect(isJob({ ...job('queued'), state: 'exploded' })).toBe(false);
    expect(isJob({ ...job('queued'), progress: null })).toBe(false);
    expect(isJob(null)).toBe(false);
    expect(isJob('text')).toBe(false);
  });
});

describe('isActiveJob', () => {
  it.each([
    ['queued', true],
    ['running', true],
    ['succeeded', false],
    ['failed', false],
    ['cancelled', false],
  ] as const)('%s is active: %s', (state, active) => {
    expect(isActiveJob({ state })).toBe(active);
  });

  it('treats a job not loaded yet as not active', () => {
    expect(isActiveJob(undefined)).toBe(false);
  });
});
