import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ClearOldResults } from '@/features/processing/ClearOldResults';
import { ProblemError } from '@/shared/http/problem';

/**
 * The button that clears the old results of a book and its confirmation, which reads from the server how many results
 * go and how much room that frees, states both, sends nothing until it is confirmed, and queues the collection then.
 */

const sdk = vi.hoisted(() => ({ report: vi.fn(), collect: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  collectableVersionsApiV1ProjectsProjectIdVersionsCollectableGet: sdk.report,
  collectVersionsApiV1ProjectsProjectIdVersionsCollectPost: sdk.collect,
}));

const FREED_BYTES = 1_572_864;

describe('ClearOldResults', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  const byId = (id: string): HTMLElement | null =>
    document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const dialog = (): string => document.body.querySelector('[role="dialog"]')?.textContent ?? '';

  async function settle(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  async function open(): Promise<void> {
    await act(async () => {
      byId('clear-old-results')?.click();
    });
    await settle();
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.report.mockReset();
    sdk.collect.mockReset();
    sdk.report.mockResolvedValue({ data: { versions: 3, size_bytes: FREED_BYTES } });
    sdk.collect.mockResolvedValue({ data: { id: 'job' } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <ClearOldResults projectId="project">
            <button type="button" data-testid="clear-old-results">
              Clear old results
            </button>
          </ClearOldResults>
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

  it('asks the server nothing until the dialog is opened, then states the count and the room it frees', async () => {
    expect(sdk.report).not.toHaveBeenCalled();

    await open();

    expect(sdk.report.mock.calls[0]?.[0]).toMatchObject({ path: { project_id: 'project' } });
    expect(dialog()).toContain('3 old results');
    expect(dialog()).toContain('frees 1.5 MB');
    expect(sdk.collect).not.toHaveBeenCalled();
  });

  it('queues the collection of the book when it is confirmed, and closes the dialog', async () => {
    await open();
    await act(async () => {
      byId('clear-old-results-submit')?.click();
    });
    await settle();

    expect(sdk.collect).toHaveBeenCalledTimes(1);
    expect(sdk.collect.mock.calls[0]?.[0]).toMatchObject({ path: { project_id: 'project' } });
    expect(byId('clear-old-results-submit')).toBeNull();
  });

  it('reads the count again each time the dialog is opened', async () => {
    await open();
    sdk.report.mockResolvedValue({ data: { versions: 1, size_bytes: 2_048 } });
    await act(async () => {
      document.body.querySelector<HTMLElement>('[role="dialog"] button')?.click();
    });
    await settle();
    await open();

    expect(sdk.report).toHaveBeenCalledTimes(2);
    expect(dialog()).toContain('1 old result ');
    expect(dialog()).toContain('frees 2 KB');
  });

  it('offers nothing to confirm when no result goes', async () => {
    sdk.report.mockResolvedValue({ data: { versions: 0, size_bytes: 0 } });
    await open();

    expect(dialog()).toContain('The book has no old results to clear.');
    expect(byId('clear-old-results-submit')?.hasAttribute('disabled')).toBe(true);
  });

  it('keeps the dialog open and shows the answer when the server refuses the collection', async () => {
    sdk.collect.mockRejectedValue(
      new ProblemError('Another job of this book is running.', 409, null, []),
    );
    await open();
    await act(async () => {
      byId('clear-old-results-submit')?.click();
    });
    await settle();

    expect(dialog()).toContain('Another job of this book is running.');
    expect(byId('clear-old-results-submit')).not.toBeNull();
  });
});
