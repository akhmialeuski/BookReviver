import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BookSchema } from '@/api';
import { bookApiV1BooksBookIdGetQueryKey } from '@/api/@tanstack/react-query.gen';
import { bookWith } from '@/features/about/fixtures';
import { type Autosave, useAutosave } from '@/features/about/useAutosave';

/**
 * The saving of the About tab: the answer to the `PATCH` of a book carries nothing the client keeps, so the book of the
 * cache stays as the server last gave it until the `book-changed` event of the change reads it again.
 *
 * The generated client is replaced by a function the test reads, so the request is seen as the server gets it.
 */

const NEW_TITLE = 'A title the server stored';

const sdk = vi.hoisted(() => ({ patch: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  updateBookApiV1BooksBookIdPatch: sdk.patch,
}));

describe('useAutosave', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let autosave: Autosave | null;
  let book: BookSchema;

  function Harness(): null {
    autosave = useAutosave(book);
    return null;
  }

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.patch.mockReset();
    // The server answers 204, which the generated client reads as no data
    sdk.patch.mockResolvedValue({ data: undefined });
    autosave = null;
    book = bookWith();
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  it('sends the change and puts nothing into the cache of the book from the answer', async () => {
    const key = bookApiV1BooksBookIdGetQueryKey({ path: { book_id: book.id } });
    client.setQueryData(key, book);
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Harness />
        </QueryClientProvider>,
      );
    });

    await act(async () => autosave?.edit({ title: NEW_TITLE }, { immediate: true }));
    await settle();
    await settle();

    expect(sdk.patch).toHaveBeenCalledTimes(1);
    expect(sdk.patch.mock.calls[0]?.[0]).toMatchObject({
      path: { book_id: book.id },
      body: { title: NEW_TITLE },
    });
    expect(autosave?.state).toBe('saved');
    expect(client.getQueryData(key)).toBe(book);
    expect(client.getQueryCache().getAll()).toHaveLength(1);
    expect(client.getQueryState(key)?.isInvalidated).toBe(false);
  });
});
