import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ContentTypeMenu } from '@/features/processing/ContentTypeMenu';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * The menu of the content type of the open page: a radio choice of the type, a radio choice of the pages it reaches, and
 * what a choice of each sends to the server.
 */

const sdk = vi.hoisted(() => ({ update: vi.fn(), jobs: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  updatePageApiV1ProjectsProjectIdPagesPageIdPatch: sdk.update,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

class SizeObserverStandIn {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const ITEMS = joinRows(
  [page('a', { position: 0 }), page('b', { position: 1 }), page('c', { position: 2 })],
  [row('a'), row('b'), row('c')],
);

describe('ContentTypeMenu', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(selected: ReadonlySet<string> = new Set()): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ContentTypeMenu projectId="book" items={ITEMS} currentId="a" selected={selected} />
        </QueryClientProvider>,
      ),
    );
  }

  async function openMenu(): Promise<void> {
    const trigger = container.querySelector<HTMLElement>('[data-testid="content-type-menu"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  const entry = (testId: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${testId}"]`);

  async function choose(testId: string): Promise<void> {
    await act(async () => {
      entry(testId)?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('ResizeObserver', SizeObserverStandIn);
    sdk.update.mockReset();
    sdk.jobs.mockReset();
    sdk.update.mockResolvedValue({ data: page('a') });
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.querySelectorAll('[role="menu"]').forEach((node) => {
      node.remove();
    });
    client.clear();
    vi.unstubAllGlobals();
  });

  it('offers the types and the pages as radio choices, with the type of the page and the pages it reaches checked', async () => {
    render(new Set(['b', 'c']));
    await openMenu();

    const radios = [...document.body.querySelectorAll('[role="menuitemradio"]')].map((node) => [
      node.getAttribute('data-testid'),
      node.getAttribute('aria-checked'),
    ]);
    expect(radios).toEqual([
      ['content-type-text', 'true'],
      ['content-type-color-picture', 'false'],
      ['content-type-bw-picture', 'false'],
      ['content-scope-page', 'true'],
      ['content-scope-selected', 'false'],
    ]);
    expect(document.body.querySelectorAll('[role="menuitem"]')).toHaveLength(1);
  });

  it('checks the selected pages when they are chosen, and keeps the menu open', async () => {
    render(new Set(['b', 'c']));
    await openMenu();

    await choose('content-scope-selected');

    expect(entry('content-scope-selected')?.getAttribute('aria-checked')).toBe('true');
    expect(entry('content-scope-page')?.getAttribute('aria-checked')).toBe('false');
    expect(document.body.querySelector('[role="menu"]')).not.toBeNull();
  });

  it('sets the type for the selected pages, and closes the menu', async () => {
    render(new Set(['b', 'c']));
    await openMenu();
    await choose('content-scope-selected');

    await choose('content-type-bw-picture');

    expect(sdk.update.mock.calls.map((call) => call[0])).toMatchObject([
      { path: { page_id: 'b' }, body: { content_type: 'bw-picture' } },
      { path: { page_id: 'c' }, body: { content_type: 'bw-picture' } },
    ]);
    expect(document.body.querySelector('[role="menu"]')).toBeNull();
  });

  it('sets the type for the open page when no page is selected', async () => {
    render();
    await openMenu();

    await choose('content-type-color-picture');

    expect(sdk.update).toHaveBeenCalledTimes(1);
    expect(sdk.update.mock.calls[0]?.[0]).toMatchObject({
      path: { page_id: 'a' },
      body: { content_type: 'color-picture' },
    });
  });
});
