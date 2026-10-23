import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSchema } from '@/api';
import {
  collectableVersionsApiV1ProjectsProjectIdVersionsCollectableGetQueryKey,
  readJobApiV1JobsJobIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { numberingPreviewKey } from '@/features/order/usePreviewLabels';
import { pagesScope, versionReadyKey } from '@/features/projects/queries';
import {
  applyProjectEvent,
  type BookResource,
  type BookSync,
  BURST_DELAY_MS,
  EventName,
  isActiveJob,
  isJob,
} from './events';

/**
 * How the events of a book change the cache: a change of the book reads again the parts it names, a version that skips
 * one or a reset after a break reads the whole book, and a job event is stored as it is.
 */

const PROJECT_ID = 'p-1';
const PAGES_READ = 'listPagesApiV1ProjectsProjectIdPagesGet';
// What the reads of the settings and the histories, which match by a predicate, are recorded as
const BY_PREDICATE = 'predicate';

function job(state: JobSchema['state'], done = 1, total = 4): JobSchema {
  return {
    id: 'j-1',
    project_id: PROJECT_ID,
    kind: 'import-source',
    stage: null,
    state,
    progress: { done, total, fraction: total === 0 ? 0 : done / total },
    error: '',
    created_at: '2026-01-01T00:00:00Z',
    started_at: null,
    finished_at: null,
  };
}

/** A change of the book from version 1 to version 2 that wrote the given parts. */
function change(resources: BookResource[], version = 2) {
  return { event: EventName.BookChanged, data: { project_id: PROJECT_ID, version, resources } };
}

/** Start recording the endpoints whose queries are invalidated; the returned function lists them. */
function watchInvalidations(queryClient: QueryClient): () => string[] {
  const spy = vi.spyOn(queryClient, 'invalidateQueries');
  return () =>
    spy.mock.calls.map(([filters]) => {
      if (filters?.predicate !== undefined) {
        return BY_PREDICATE;
      }
      const key = filters?.queryKey;
      const head = Array.isArray(key) ? key[0] : undefined;
      return typeof head === 'object' && head !== null && '_id' in head ? String(head._id) : '';
    });
}

/** Hold a change of the pages of a book in flight, as a move the server has not answered yet. */
function holdAChangeOfThePages(queryClient: QueryClient, projectId: string): void {
  void queryClient
    .getMutationCache()
    .build(queryClient, {
      mutationFn: () => new Promise(() => undefined),
      scope: pagesScope(projectId),
    })
    .execute(undefined);
}

describe('applyProjectEvent', () => {
  let queryClient: QueryClient;
  let invalidated: () => string[];
  let sync: BookSync;

  beforeEach(() => {
    vi.useFakeTimers();
    queryClient = new QueryClient();
    invalidated = watchInvalidations(queryClient);
    sync = { version: 1 };
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([
    ['project', 'projectApiV1ProjectsProjectIdGet'],
    ['sources', 'listSourcesApiV1ProjectsProjectIdSourcesGet'],
    ['scans', 'listScansApiV1ProjectsProjectIdScansGet'],
    ['pages', 'listPaginationSectionsApiV1ProjectsProjectIdPaginationSectionsGet'],
    ['versions', 'listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGet'],
    ['stages', 'listStagesApiV1ProjectsProjectIdStagesGet'],
    ['settings', BY_PREDICATE],
    ['history', BY_PREDICATE],
    ['recipes', 'listRecipesApiV1ProjectsProjectIdStagesStageRecipesGet'],
  ] as const)('reads %s again once the burst of a change of it ends', (resource, read) => {
    applyProjectEvent(queryClient, PROJECT_ID, change([resource]), sync);
    expect(invalidated()).toEqual([]);

    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toContain(read);
    expect(sync.version).toBe(2);
  });

  it('marks the previews of a numbering stale when the pages change, and the count of a collection when the versions do', () => {
    const preview = [...numberingPreviewKey(PROJECT_ID), { first: 'pg-1' }];
    const collectable = collectableVersionsApiV1ProjectsProjectIdVersionsCollectableGetQueryKey({
      path: { project_id: PROJECT_ID },
    });
    queryClient.setQueryData(preview, new Map());
    queryClient.setQueryData(collectable, { versions: 0, bytes: 0 });

    applyProjectEvent(queryClient, PROJECT_ID, change(['pages']), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);
    expect(queryClient.getQueryState(preview)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(collectable)?.isInvalidated).toBe(false);

    applyProjectEvent(queryClient, PROJECT_ID, change(['versions'], 3), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);
    expect(queryClient.getQueryState(collectable)?.isInvalidated).toBe(true);
  });

  it('reads a part once for a burst of changes of it, one per page of a run', () => {
    for (let version = 2; version <= 20; version += 1) {
      applyProjectEvent(queryClient, PROJECT_ID, change(['stages'], version), sync);
    }
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(
      invalidated().filter((read) => read === 'listStagesApiV1ProjectsProjectIdStagesGet'),
    ).toHaveLength(1);
  });

  it('leaves the pages alone while a change of the reader is in flight, and reads what depends on them', () => {
    // The change that is in flight reads the pages again when it ends, and by then sees what this event announces
    holdAChangeOfThePages(queryClient, PROJECT_ID);

    applyProjectEvent(queryClient, PROJECT_ID, change(['pages']), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).not.toContain(PAGES_READ);
    expect(invalidated()).toContain('listStagesApiV1ProjectsProjectIdStagesGet');
  });

  it('does not hold back the pages for a change of another book', () => {
    holdAChangeOfThePages(queryClient, 'p-2');

    applyProjectEvent(queryClient, PROJECT_ID, change(['pages']), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toContain(PAGES_READ);
  });

  it('reads the whole book at once when a version is skipped, since a change was lost', () => {
    applyProjectEvent(queryClient, PROJECT_ID, change(['project'], 3), sync);

    expect(invalidated()).toContain('listRecipesApiV1ProjectsProjectIdStagesStageRecipesGet');
    expect(invalidated()).toContain('listProjectJobsApiV1ProjectsProjectIdJobsGet');
    expect(sync.version).toBe(3);
  });

  it('takes the first change it sees as it is when it knows no version yet', () => {
    sync = { version: null };

    applyProjectEvent(queryClient, PROJECT_ID, change(['project'], 7), sync);

    expect(invalidated()).toEqual([]);
    expect(sync.version).toBe(7);
  });

  it('keeps the version the stream opens with and reads nothing', () => {
    sync = { version: null };

    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      { event: EventName.BookVersion, data: { project_id: PROJECT_ID, version: 4 } },
      sync,
    );

    expect(sync.version).toBe(4);
    expect(invalidated()).toEqual([]);
  });

  it('reads the whole book after a reset and keeps the version it names', () => {
    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      { event: EventName.BookReset, data: { project_id: PROJECT_ID, version: 9 } },
      sync,
    );

    expect(sync.version).toBe(9);
    expect(invalidated()).toContain(PAGES_READ);
    expect(invalidated()).toContain('listSourcesApiV1ProjectsProjectIdSourcesGet');
  });

  it('ignores a change whose data names no version', () => {
    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      { event: EventName.BookChanged, data: { resources: [] } },
      sync,
    );
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toEqual([]);
    expect(sync.version).toBe(1);
  });

  it('writes the job of a running job-changed event into the query of that job', () => {
    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      { event: EventName.JobChanged, data: job('running') },
      sync,
    );

    expect(
      queryClient.getQueryData(readJobApiV1JobsJobIdGetQueryKey({ path: { job_id: 'j-1' } })),
    ).toEqual(job('running'));
  });

  it('reads the reads that show the jobs when a job ends, and not the pages', async () => {
    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      { event: EventName.JobChanged, data: job('succeeded', 4) },
      sync,
    );
    // The rows of the stages cancel their reads in flight first, and ask for the new read a turn later
    await vi.advanceTimersByTimeAsync(0);

    // The rows are read once for each stage, so the endpoints are compared as a set
    expect([...new Set(invalidated())].sort()).toEqual([
      'listProjectJobsApiV1ProjectsProjectIdJobsGet',
      'listProjectsApiV1ProjectsGet',
      'listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet',
      'listStagesApiV1ProjectsProjectIdStagesGet',
      'projectApiV1ProjectsProjectIdGet',
    ]);
  });

  it('marks the jobs stale once for a burst of progress events of a running job', () => {
    for (let done = 1; done <= 20; done += 1) {
      applyProjectEvent(
        queryClient,
        'burst-5',
        { event: EventName.JobChanged, data: job('running', done, 20) },
        sync,
      );
    }
    expect(invalidated()).toEqual([]);

    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toEqual(['listProjectJobsApiV1ProjectsProjectIdJobsGet']);
  });

  it('keeps the version a page-version-ready event names and reads nothing', () => {
    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      {
        event: EventName.PageVersionReady,
        data: { project_id: PROJECT_ID, page_id: 'pg-1', version_id: 'abc' },
      },
      sync,
    );

    expect(queryClient.getQueryData(versionReadyKey(PROJECT_ID, 'pg-1'))).toMatchObject({
      versionId: 'abc',
    });
    expect(invalidated()).toEqual([]);
  });

  it('shows a version announced twice as a new event', () => {
    const event = {
      event: EventName.PageVersionReady,
      data: { project_id: PROJECT_ID, page_id: 'pg-1', version_id: 'abc' },
    };
    vi.setSystemTime(1000);
    applyProjectEvent(queryClient, PROJECT_ID, event, sync);
    vi.setSystemTime(2000);
    applyProjectEvent(queryClient, PROJECT_ID, event, sync);

    expect(queryClient.getQueryData(versionReadyKey(PROJECT_ID, 'pg-1'))).toEqual({
      versionId: 'abc',
      at: 2000,
    });
  });

  it('ignores a job-changed event whose data is not a job', () => {
    applyProjectEvent(
      queryClient,
      PROJECT_ID,
      { event: EventName.JobChanged, data: { id: 5 } },
      sync,
    );

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(invalidated()).toEqual([]);
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
