import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing } from '@/features/processing/fixtures';
import { MeasureBook } from '@/features/processing/MeasureBook';

/**
 * The button that measures the book, in the panel of the normalize step: it asks the server for the job, and waits while
 * the draft is not saved or a job of the book is going.
 *
 * The generated client is replaced by functions the test reads, so what the button sends is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({ measure: vi.fn(), jobs: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  measureBookApiV1ProjectsProjectIdStagesGeometryMeasurePost: sdk.measure,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

const BUTTON = '[data-testid="measure-book-button"]';
const NO_JOBS = { data: { items: [], total: 0, page: 1, size: 20, pages: 1 } };

describe('MeasureBook', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(dirty = false): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <MeasureBook processing={processing({ dirty })} />
        </QueryClientProvider>,
      ),
    );
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.measure.mockReset();
    sdk.jobs.mockReset();
    sdk.measure.mockResolvedValue({ data: { id: 'job' } });
    sdk.jobs.mockResolvedValue(NO_JOBS);
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

  it('asks the server to measure the book of the stage', async () => {
    render();

    await act(async () => {
      container.querySelector<HTMLButtonElement>(BUTTON)?.click();
    });

    expect(sdk.measure).toHaveBeenCalledTimes(1);
    expect(sdk.measure.mock.calls[0]?.[0]).toMatchObject({ path: { project_id: 'project' } });
  });

  it('waits for the recipe to be saved, since the job writes the saved recipe', () => {
    render(true);

    expect(container.querySelector<HTMLButtonElement>(BUTTON)?.disabled).toBe(true);
    expect(container.textContent).toContain('Save the recipe before measuring the book.');
  });

  it('waits while another job of the book is going', async () => {
    sdk.jobs.mockResolvedValue({
      data: {
        items: [
          {
            id: 'j',
            state: 'running',
            kind: 'run-stage',
            progress: { done: 0, total: 1, fraction: 0 },
          },
        ],
        total: 1,
        page: 1,
        size: 20,
        pages: 1,
      },
    });
    render();

    await vi.waitFor(() => {
      expect(container.querySelector<HTMLButtonElement>(BUTTON)?.disabled).toBe(true);
    });
  });
});
