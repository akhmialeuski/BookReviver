import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSchema } from '@/api';
import {
  collectableVersionsApiV1BooksBookIdVersionsCollectableGetQueryKey,
  listStagePagesApiV1BooksBookIdStagesStagePagesGetQueryKey,
  readJobApiV1BooksBookIdJobsJobIdGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import { numberingPreviewKey, pagesScope, versionReadyKey } from '@/features/books/queries';
import { STAGES } from '@/features/stages/stages';
import {
  applyBookEvent,
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

const BOOK_ID = 'p-1';
const PAGES_READ = 'listPagesApiV1BooksBookIdPagesGet';
// What the reads of the settings and the histories, which match by a predicate, are recorded as
const BY_PREDICATE = 'predicate';

function job(state: JobSchema['state'], done = 1, total = 4): JobSchema {
  return {
    id: 'j-1',
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
  return { event: EventName.BookChanged, data: { version, resources } };
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
function holdAChangeOfThePages(queryClient: QueryClient, bookId: string): void {
  void queryClient
    .getMutationCache()
    .build(queryClient, {
      mutationFn: () => new Promise(() => undefined),
      scope: pagesScope(bookId),
    })
    .execute(undefined);
}

describe('applyBookEvent', () => {
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
    ['record', 'bookApiV1BooksBookIdGet'],
    ['sources', 'listSourcesApiV1BooksBookIdSourcesGet'],
    ['scans', 'listScansApiV1BooksBookIdScansGet'],
    ['pages', 'listPaginationSectionsApiV1BooksBookIdPaginationSectionsGet'],
    ['versions', 'listVersionsApiV1BooksBookIdPagesPageIdVersionsGet'],
    ['stages', 'listStagesApiV1BooksBookIdStagesGet'],
    ['settings', BY_PREDICATE],
    ['history', BY_PREDICATE],
    ['recipes', 'listRecipesApiV1BooksBookIdStagesStageRecipesGet'],
  ] as const)('reads %s again at once when a change of it arrives alone', (resource, read) => {
    applyBookEvent(queryClient, BOOK_ID, change([resource]), sync);

    expect(invalidated()).toContain(read);
    expect(sync.version).toBe(2);
  });

  it('reads the rows of the stages again when the settings of a page change, since a row says what a step holds on a page', () => {
    // An undo on a page whose stage is out of date already writes no record of a stage, so the change names the settings alone
    applyBookEvent(queryClient, BOOK_ID, change(['settings']), sync);

    expect(invalidated()).toContain('listStagePagesApiV1BooksBookIdStagesStagePagesGet');
  });

  it('reads the status of the stages and the book again when a version changes, since a good mark takes a page out of the check', () => {
    // A mark set on a result writes the version alone, and no record of a stage
    applyBookEvent(queryClient, BOOK_ID, change(['versions']), sync);

    expect(invalidated()).toEqual(
      expect.arrayContaining(['listStagesApiV1BooksBookIdStagesGet', 'bookApiV1BooksBookIdGet']),
    );
  });

  it('marks the previews of a numbering stale when the pages change, and the count of a collection when the versions do', () => {
    const preview = [...numberingPreviewKey(BOOK_ID), { first: 'pg-1' }];
    const collectable = collectableVersionsApiV1BooksBookIdVersionsCollectableGetQueryKey({
      path: { book_id: BOOK_ID },
    });
    queryClient.setQueryData(preview, new Map());
    queryClient.setQueryData(collectable, { versions: 0, bytes: 0 });

    applyBookEvent(queryClient, BOOK_ID, change(['pages']), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);
    expect(queryClient.getQueryState(preview)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(collectable)?.isInvalidated).toBe(false);

    applyBookEvent(queryClient, BOOK_ID, change(['versions'], 3), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);
    expect(queryClient.getQueryState(collectable)?.isInvalidated).toBe(true);
  });

  it('reads a part at the start and at the end of a burst of changes of it, one per page of a run', () => {
    const stageReads = () =>
      invalidated().filter((read) => read === 'listStagesApiV1BooksBookIdStagesGet');
    for (let version = 2; version <= 20; version += 1) {
      applyBookEvent(queryClient, BOOK_ID, change(['stages'], version), sync);
    }
    expect(stageReads()).toHaveLength(1);

    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(stageReads()).toHaveLength(2);
  });

  it('reads the rows of each stage once for a change that names several parts drawn in them', () => {
    applyBookEvent(
      queryClient,
      BOOK_ID,
      change(['versions', 'stages', 'settings', 'history']),
      sync,
    );

    const reads = invalidated();
    expect(
      reads.filter((read) => read === 'listStagePagesApiV1BooksBookIdStagesStagePagesGet'),
    ).toHaveLength(STAGES.length);
    // The layers of the pages are read with their histories, so the histories are not read a second time
    expect(reads.filter((read) => read === BY_PREDICATE)).toHaveLength(1);
  });

  it('leaves the pages alone while a change of the reader is in flight, and reads what depends on them', () => {
    // The change that is in flight reads the pages again when it ends, and by then sees what this event announces
    holdAChangeOfThePages(queryClient, BOOK_ID);

    applyBookEvent(queryClient, BOOK_ID, change(['pages']), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).not.toContain(PAGES_READ);
    expect(invalidated()).toContain('listStagesApiV1BooksBookIdStagesGet');
  });

  it('does not hold back the pages for a change of another book', () => {
    holdAChangeOfThePages(queryClient, 'p-2');

    applyBookEvent(queryClient, BOOK_ID, change(['pages']), sync);
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toContain(PAGES_READ);
  });

  it('reads the whole book at once when a version is skipped, since a change was lost', () => {
    applyBookEvent(queryClient, BOOK_ID, change(['record'], 3), sync);

    expect(invalidated()).toContain('listRecipesApiV1BooksBookIdStagesStageRecipesGet');
    expect(invalidated()).toContain('listBookJobsApiV1BooksBookIdJobsGet');
    expect(sync.version).toBe(3);
  });

  it('takes the first change it sees as it is when it knows no version yet, and reads only what it names', () => {
    sync = { version: null };

    applyBookEvent(queryClient, BOOK_ID, change(['record'], 7), sync);

    expect(invalidated()).toContain('bookApiV1BooksBookIdGet');
    expect(invalidated()).not.toContain('listRecipesApiV1BooksBookIdStagesStageRecipesGet');
    expect(sync.version).toBe(7);
  });

  it('keeps the version the stream opens with and reads nothing', () => {
    sync = { version: null };

    applyBookEvent(
      queryClient,
      BOOK_ID,
      { event: EventName.BookVersion, data: { version: 4 } },
      sync,
    );

    expect(sync.version).toBe(4);
    expect(invalidated()).toEqual([]);
  });

  it('reads the whole book after a reset and keeps the version it names', () => {
    applyBookEvent(
      queryClient,
      BOOK_ID,
      { event: EventName.BookReset, data: { version: 9 } },
      sync,
    );

    expect(sync.version).toBe(9);
    expect(invalidated()).toContain(PAGES_READ);
    expect(invalidated()).toContain('listSourcesApiV1BooksBookIdSourcesGet');
  });

  it('ignores a change whose data names no version', () => {
    applyBookEvent(
      queryClient,
      BOOK_ID,
      { event: EventName.BookChanged, data: { resources: [] } },
      sync,
    );
    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toEqual([]);
    expect(sync.version).toBe(1);
  });

  it('writes the job of a running job-changed event into the query of that job', () => {
    applyBookEvent(
      queryClient,
      BOOK_ID,
      { event: EventName.JobChanged, data: job('running') },
      sync,
    );

    expect(
      queryClient.getQueryData(
        readJobApiV1BooksBookIdJobsJobIdGetQueryKey({ path: { book_id: BOOK_ID, job_id: 'j-1' } }),
      ),
    ).toEqual(job('running'));
  });

  it('reads the reads that show the jobs when a job ends, and not the pages', () => {
    applyBookEvent(
      queryClient,
      BOOK_ID,
      { event: EventName.JobChanged, data: job('succeeded', 4) },
      sync,
    );

    // The rows are read once for each stage, so the endpoints are compared as a set
    expect([...new Set(invalidated())].sort()).toEqual([
      'bookApiV1BooksBookIdGet',
      'listBookJobsApiV1BooksBookIdJobsGet',
      'listBooksApiV1BooksGet',
      'listStagePagesApiV1BooksBookIdStagesStagePagesGet',
      'listStagesApiV1BooksBookIdStagesGet',
    ]);
  });

  it('marks the jobs stale at the start and at the end of a burst of progress events of a running job', () => {
    const JOBS_READ = 'listBookJobsApiV1BooksBookIdJobsGet';
    for (let done = 1; done <= 20; done += 1) {
      applyBookEvent(
        queryClient,
        'burst-5',
        { event: EventName.JobChanged, data: job('running', done, 20) },
        sync,
      );
    }
    expect(invalidated()).toEqual([JOBS_READ]);

    vi.advanceTimersByTime(BURST_DELAY_MS);

    expect(invalidated()).toEqual([JOBS_READ, JOBS_READ]);
  });

  it('keeps the version a page-version-ready event names and reads nothing', () => {
    applyBookEvent(
      queryClient,
      BOOK_ID,
      {
        event: EventName.PageVersionReady,
        data: { page_id: 'pg-1', version_id: 'abc' },
      },
      sync,
    );

    expect(queryClient.getQueryData(versionReadyKey(BOOK_ID, 'pg-1'))).toMatchObject({
      versionId: 'abc',
    });
    expect(invalidated()).toEqual([]);
  });

  it('shows a version announced twice as a new event', () => {
    const event = {
      event: EventName.PageVersionReady,
      data: { page_id: 'pg-1', version_id: 'abc' },
    };
    vi.setSystemTime(1000);
    applyBookEvent(queryClient, BOOK_ID, event, sync);
    vi.setSystemTime(2000);
    applyBookEvent(queryClient, BOOK_ID, event, sync);

    expect(queryClient.getQueryData(versionReadyKey(BOOK_ID, 'pg-1'))).toEqual({
      versionId: 'abc',
      at: 2000,
    });
  });

  it('ignores a job-changed event whose data is not a job', () => {
    applyBookEvent(queryClient, BOOK_ID, { event: EventName.JobChanged, data: { id: 5 } }, sync);

    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(invalidated()).toEqual([]);
  });
});

describe('the rows of a stage during a run', () => {
  // A read of the rows of a step, which takes longer under a run than the bursts of its changes are apart
  const READ_MS = 1000;
  // A run commits a change of the book for each step of each page, far more often than a burst is folded
  const CHANGE_EVERY_MS = 100;
  const RUN_MS = 5000;

  let queryClient: QueryClient;
  let sync: BookSync;
  let unsubscribe: () => void;

  beforeEach(() => {
    vi.useFakeTimers();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    sync = { version: 1 };
    unsubscribe = () => undefined;
  });

  afterEach(() => {
    unsubscribe();
    queryClient.clear();
    vi.useRealTimers();
  });

  /** Open the rows of a step, as the strip does, each read of which answers after `READ_MS` with what it counts. */
  function openStep() {
    let reads = 0;
    const queryFn = vi.fn(({ signal }: { signal: AbortSignal }) => {
      reads += 1;
      const answer = `rows ${reads}`;
      return new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => resolve(answer), READ_MS);
        signal.addEventListener('abort', () => {
          clearTimeout(timer);
          reject(new Error('cancelled'));
        });
      });
    });
    const observer = new QueryObserver<string>(queryClient, {
      queryKey: listStagePagesApiV1BooksBookIdStagesStagePagesGetQueryKey({
        path: { book_id: BOOK_ID, stage: 'geometry' },
        query: { step: 'deskew' },
      }),
      queryFn,
    });
    unsubscribe = observer.subscribe(() => undefined);
    return { observer, queryFn };
  }

  /** Send the changes of a run of the stage for some time, while the rows are read. */
  async function run(forMs: number): Promise<void> {
    for (let at = 0; at < forMs; at += CHANGE_EVERY_MS) {
      applyBookEvent(
        queryClient,
        BOOK_ID,
        change(['versions', 'stages'], (sync.version ?? 1) + 1),
        sync,
      );
      await vi.advanceTimersByTimeAsync(CHANGE_EVERY_MS);
    }
  }

  it('shows the rows of a step opened without a change of the book after one read', async () => {
    const { observer, queryFn } = openStep();

    await vi.advanceTimersByTimeAsync(READ_MS);

    expect(observer.getCurrentResult().data).toBe('rows 1');
    expect(queryFn).toHaveBeenCalledTimes(1);
  });

  it('shows the rows of a step opened while a run changes the book, before the run ends', async () => {
    const { observer } = openStep();

    await run(READ_MS + CHANGE_EVERY_MS);

    // The first read ends and shows its rows, though the book changed while it was in flight
    expect(observer.getCurrentResult().data).toBe('rows 1');

    await run(RUN_MS);

    // The rows follow the run, about one read after another, and are not cancelled by the next change
    const shown = observer.getCurrentResult().data ?? '';
    expect(Number(shown.split(' ')[1])).toBeGreaterThanOrEqual(RUN_MS / READ_MS);
  });

  it('reads the rows once more after the run, so the last change shows', async () => {
    const { observer, queryFn } = openStep();

    await run(RUN_MS);
    const during = queryFn.mock.calls.length;
    // A read is in flight when the run ends, and the last change came while it was
    await vi.advanceTimersByTimeAsync(3 * READ_MS);

    expect(queryFn.mock.calls.length).toBeGreaterThan(during);
    expect(observer.getCurrentResult().data).toBe(`rows ${queryFn.mock.calls.length}`);
    expect(observer.getCurrentResult().isFetching).toBe(false);
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
