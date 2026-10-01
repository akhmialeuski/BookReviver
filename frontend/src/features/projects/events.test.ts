import { QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSchema } from '@/api';
import { readJobApiV1JobsJobIdGetQueryKey } from '@/api/@tanstack/react-query.gen';
import { applyProjectEvent, EventName, isActiveJob, isJob } from './events';

/**
 * How events change the cache: a job event is stored as it is, the others mark the affected lists stale.
 */

const PROJECT_ID = 'p-1';

function job(state: JobSchema['state'], done = 1, total = 4): JobSchema {
  return {
    id: 'j-1',
    project_id: PROJECT_ID,
    kind: 'import-source',
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

    expect(invalidated().sort()).toEqual([
      'listProjectsApiV1ProjectsGet',
      'listScansApiV1ProjectsProjectIdScansGet',
      'listSourcesApiV1ProjectsProjectIdSourcesGet',
      'projectApiV1ProjectsProjectIdGet',
    ]);
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
        'listProjectsApiV1ProjectsGet',
        'listSourcesApiV1ProjectsProjectIdSourcesGet',
        'projectApiV1ProjectsProjectIdGet',
      ],
    ],
    [EventName.ScanReady, ['listScansApiV1ProjectsProjectIdScansGet']],
    [EventName.PagesChanged, ['listProjectsApiV1ProjectsGet', 'projectApiV1ProjectsProjectIdGet']],
    [
      EventName.ProjectChanged,
      ['listProjectsApiV1ProjectsGet', 'projectApiV1ProjectsProjectIdGet'],
    ],
    [EventName.PageVersionReady, []],
  ])('on %s marks %j stale', (name, expected) => {
    applyProjectEvent(queryClient, PROJECT_ID, { event: name, data: {} });

    expect(invalidated().sort()).toEqual(expected);
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
