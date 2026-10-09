import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey,
  listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey,
} from '@/api/@tanstack/react-query.gen';
import {
  invalidateStageRows,
  invalidateVersions,
  refreshQueries,
} from '@/features/projects/queries';

/**
 * The refresh that follows a change the screen has just made: a read that is still in flight was asked for before the
 * change, so the refresh must not join it.
 */

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('refreshQueries', () => {
  let client: QueryClient;
  let unsubscribe: () => void;

  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    unsubscribe = () => undefined;
  });

  afterEach(() => {
    unsubscribe();
    client.clear();
  });

  /**
   * Watch a query whose first read is held back, as a screen does that has just opened, and return what it reads.
   *
   * @param queryKey The key of the query.
   * @returns The first read, to be resolved with the old data, the function every read goes through, and the observer.
   */
  function watch(queryKey: readonly unknown[]) {
    const first = deferred<string>();
    const queryFn = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue('new');
    const observer = new QueryObserver<string>(client, { queryKey, queryFn });
    unsubscribe = observer.subscribe(() => undefined);
    return { first, queryFn, observer };
  }

  it('reads again, after the change, a query whose first read is still in flight', async () => {
    const key = ['rows'];
    const { first, queryFn, observer } = watch(key);

    await refreshQueries(client, key);
    // The first read was asked for before the change and answers with what the change has since replaced
    first.resolve('old');

    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe('new'));
    expect(queryFn).toHaveBeenCalledTimes(2);
  });

  it('reads again the rows of a stage whose first read is still in flight', async () => {
    const key = listStagePagesApiV1ProjectsProjectIdStagesStagePagesGetQueryKey({
      path: { project_id: 'book', stage: 'cleanup' },
    });
    const { first, queryFn, observer } = watch(key);

    await invalidateStageRows(client, 'book', 'cleanup');
    first.resolve('old');

    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe('new'));
    expect(queryFn).toHaveBeenCalledTimes(2);
  });

  it('reads again the results of a page whose first read is still in flight', async () => {
    const key = listVersionsApiV1ProjectsProjectIdPagesPageIdVersionsGetQueryKey({
      path: { project_id: 'book', page_id: 'page' },
      query: { stage: 'cleanup' },
    });
    const { first, queryFn, observer } = watch(key);

    await invalidateVersions(client, 'book', 'page');
    first.resolve('old');

    await vi.waitFor(() => expect(observer.getCurrentResult().data).toBe('new'));
    expect(queryFn).toHaveBeenCalledTimes(2);
  });
});
