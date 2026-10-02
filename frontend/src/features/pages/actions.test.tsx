import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageCreate, PageSchema } from '@/api';
import { useCreatePages, useUpdatePages } from '@/features/pages/actions';
import { manifestOptions } from '@/features/pages/manifest';
import { page } from '@/features/workspace/fixtures';

/**
 * The cache of the manifest while a change of pages is applied to it before the server answers.
 *
 * A read of the manifest is often in flight when a change is made, since every earlier change makes the manifest be
 * read again. Cancelling that read must leave the query a success with its data: a query that falls into the error
 * state turns the screen that shows the pages into an error box until the next read lands.
 */

// The requests answer only when a test says so, so a change stays pending and what is done around it can be seen
const answers = vi.hoisted(() => [] as (() => void)[]);

// The one request that adds several pages, whose answer a test sets
const addPages = vi.hoisted(() => vi.fn());

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  updatePageApiV1ProjectsProjectIdPagesPageIdPatch: vi.fn(
    () => new Promise((resolve) => answers.push(() => resolve({ data: {} }))),
  ),
  createPagesApiV1ProjectsProjectIdPagesBatchPost: addPages,
}));

const PROJECT = 'book';

describe('a change of pages while the manifest is being read', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let change: (() => void) | undefined;

  function Probe(): null {
    const update = useUpdatePages(PROJECT);
    change = () => update.mutate({ pageIds: ['a'], changes: { kind: 'plate' } });
    return null;
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    answers.length = 0;
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient();
    client.setQueryData(manifestOptions(PROJECT).queryKey, [page('a'), page('b')]);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  it('keeps the query a success with the change made when it cancels a read in flight', async () => {
    const { queryKey } = manifestOptions(PROJECT);
    // A read that never ends stands for the one the last change started
    void client
      .fetchQuery({
        queryKey,
        queryFn: () => new Promise<PageSchema[]>(() => undefined),
        staleTime: 0,
      })
      .catch(() => undefined);
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe />
        </QueryClientProvider>,
      ),
    );
    expect(client.getQueryState(queryKey)?.fetchStatus).toBe('fetching');

    await act(async () => change?.());
    await vi.waitFor(() => expect(client.getQueryState(queryKey)?.fetchStatus).toBe('idle'));

    expect(client.getQueryState(queryKey)?.status).toBe('success');
    expect(client.getQueryData<PageSchema[]>(queryKey)?.map((entry) => entry.kind)).toEqual([
      'plate',
      'text',
    ]);
  });

  it('reads the pages again only when the last of the queued changes has ended', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue();
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe />
        </QueryClientProvider>,
      ),
    );

    // The second change waits behind the first, which is how the scope runs them
    await act(async () => {
      change?.();
      change?.();
    });
    expect(answers).toHaveLength(1);

    answers[0]?.();
    await vi.waitFor(() => expect(answers).toHaveLength(2));
    // The first change has ended and the second has not, so a read now would lack the second change
    expect(invalidate).not.toHaveBeenCalled();

    answers[1]?.();
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
  });
});

describe('the adding of several pages', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let add: ((bodies: PageCreate[]) => void) | undefined;
  let change: (() => void) | undefined;

  function Probe(): null {
    const create = useCreatePages(PROJECT);
    const update = useUpdatePages(PROJECT);
    add = (bodies) => create.mutate(bodies);
    change = () => update.mutate({ pageIds: ['a'], changes: { kind: 'plate' } });
    return null;
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    answers.length = 0;
    addPages.mockReset();
    addPages.mockResolvedValue({ data: [] });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient();
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Probe />
        </QueryClientProvider>,
      ),
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  it('sends every page in one request, in the order listed', async () => {
    const bodies: PageCreate[] = [
      { origin: 'placeholder', kind: 'text', label: '47', before_page_id: 'c' },
      { origin: 'placeholder', kind: 'text', label: '48', before_page_id: 'c' },
      { origin: 'blank', kind: 'blank' },
    ];

    await act(async () => add?.(bodies));

    await vi.waitFor(() => expect(addPages).toHaveBeenCalledTimes(1));
    expect(addPages).toHaveBeenCalledWith({
      path: { project_id: PROJECT },
      body: bodies,
      throwOnError: true,
    });
  });

  it('reads the pages again whether the request succeeds or the server refuses a page', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue();
    addPages.mockRejectedValueOnce(new Error('The page at index 1 of the list cannot be added'));

    await act(async () => add?.([{ origin: 'placeholder', kind: 'text' }]));

    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(addPages).toHaveBeenCalledTimes(1);
  });

  it('waits for a change of pages in flight and reads the pages once, after both have ended', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue();

    await act(async () => {
      change?.();
      add?.([{ origin: 'placeholder', kind: 'text' }]);
    });
    // The new pages are not requested before the change ends, so no read of the change can come before them
    expect(answers).toHaveLength(1);
    expect(addPages).not.toHaveBeenCalled();

    answers[0]?.();
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalled());
    // One read of the pages, the project and the project list, and it comes after the request for the new pages
    expect(invalidate).toHaveBeenCalledTimes(3);
    expect(addPages.mock.invocationCallOrder[0]).toBeLessThan(
      invalidate.mock.invocationCallOrder[0] ?? 0,
    );
  });
});
