import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { RunControls } from '@/features/processing/RunControls';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * The foot of the panel: what it counts, and what the run sends for each of the pages it can go over.
 *
 * The generated client is replaced by a function the test reads, so the body of every run is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({ run: vi.fn(), jobs: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
}));

const ITEMS = joinRows(
  [
    page('a', { position: 0 }),
    page('b', { position: 1 }),
    page('c', { position: 2 }),
    page('d', { position: 3 }),
  ],
  [
    row('a', { status: 'fresh' }),
    row('b', { status: 'stale' }),
    row('c', { status: 'failed' }),
    row('d', { status: 'not-run' }),
  ],
);

describe('RunControls', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(
    state = processing(),
    selected: ReadonlySet<string> = new Set(['c', 'd']),
    items = ITEMS,
  ): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <RunControls processing={state} items={items} current={items[1]} selected={selected} />
        </QueryClientProvider>,
      ),
    );
  }

  /** Open the menu of the run the way a keyboard does, which Radix answers in jsdom as it does in a browser. */
  async function openMenu(): Promise<void> {
    const trigger = container.querySelector<HTMLElement>('[data-testid="run-menu"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  async function choose(scope: string): Promise<void> {
    await openMenu();
    const item = document.body.querySelector<HTMLElement>(`[data-testid="run-${scope}"]`);
    await act(async () => {
      item?.click();
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.run.mockReset();
    sdk.jobs.mockReset();
    sdk.run.mockResolvedValue({ data: { id: 'job' } });
    sdk.jobs.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    container = document.createElement('div');
    document.body.append(container);
    root = createRoot(container);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    document.body.querySelectorAll('[role="menu"], [role="dialog"]').forEach((node) => {
      node.remove();
    });
    client.clear();
    vi.unstubAllGlobals();
  });

  it('counts the pages out of date and the pages that failed', () => {
    render();

    expect(container.querySelector('[data-testid="run-summary"]')?.textContent).toBe(
      '1 page out of date1 failed',
    );
  });

  it('says every page is up to date when none is out of date or failed', () => {
    render(
      processing(),
      new Set(),
      joinRows(
        ITEMS.map((item) => item.page),
        [row('a'), row('b')],
      ),
    );

    expect(container.querySelector('[data-testid="run-summary"]')?.textContent).toBe(
      'Every page is up to date.',
    );
  });

  it('runs the open page alone for this page', async () => {
    render();

    await choose('page');

    expect(sdk.run).toHaveBeenCalledTimes(1);
    expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
      path: { project_id: 'project', stage: 'geometry' },
      body: { recipe_id: 'r1', page_ids: ['b'] },
    });
  });

  it('runs the selected pages', async () => {
    render();

    await choose('selected');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'r1', page_ids: ['c', 'd'] });
  });

  it('runs the pages out of date and the pages that failed, and no others', async () => {
    render();

    await choose('attention');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'r1', page_ids: ['b', 'c'] });
  });

  it('runs every page by naming none, which the server reads as every page with an image', async () => {
    render();

    await choose('all');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'r1' });
  });

  it('runs the recipe the panel shows and not the active one', async () => {
    const variant = recipe('r2', { name: 'Gentle', active: false });
    render(processing({ recipe: variant, recipes: [recipe('r1'), variant] }));

    await choose('all');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'r2' });
  });

  it('keeps the run off while the draft has changes that are not saved', () => {
    render(processing({ dirty: true }));

    expect(container.querySelector<HTMLButtonElement>('[data-testid="run-menu"]')?.disabled).toBe(
      true,
    );
    expect(container.textContent).toContain('Save the recipe to run it.');
  });

  it('keeps the run off while another job of the book is going', async () => {
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
    // The jobs of the book are read, which takes a few turns of the queue
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(container.querySelector<HTMLButtonElement>('[data-testid="run-menu"]')?.disabled).toBe(
      true,
    );
  });

  describe('a run that would send a scan back to one page', () => {
    const whole = recipe('whole', { stage: 'page-split', steps: [step('split.none')] });
    const halves = joinRows(
      [page('l', { slot: 1, scan_id: 's' }), page('r', { slot: 2, scan_id: 's' })],
      [row('l', { status: 'stale' }), row('r', { status: 'stale' })],
    );

    it('asks first and sends nothing before the answer', async () => {
      render(
        processing({ stage: 'page-split', recipe: whole, recipes: [whole] }),
        new Set(),
        halves,
      );

      await choose('all');

      expect(document.body.querySelector('[role="dialog"]')?.textContent).toContain(
        'Go back to one page?',
      );
      expect(sdk.run).not.toHaveBeenCalled();
    });

    it('sends the run with the confirmation once it is given', async () => {
      render(
        processing({ stage: 'page-split', recipe: whole, recipes: [whole] }),
        new Set(),
        halves,
      );
      await choose('all');

      await act(async () => {
        document.body.querySelector<HTMLElement>('[data-testid="unsplit-confirm"]')?.click();
      });

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
        recipe_id: 'whole',
        confirm_unsplit: true,
      });
    });

    it('sends nothing when the answer is no', async () => {
      render(
        processing({ stage: 'page-split', recipe: whole, recipes: [whole] }),
        new Set(),
        halves,
      );
      await choose('all');

      await act(async () => {
        const buttons = [...document.body.querySelectorAll<HTMLElement>('[role="dialog"] button')];
        buttons.find((button) => button.textContent === 'Keep two pages')?.click();
      });

      expect(sdk.run).not.toHaveBeenCalled();
    });

    it('does not ask for a recipe that cuts, which undoes nothing', async () => {
      const spread = recipe('spread', { stage: 'page-split', steps: [step('split.spread')] });
      render(
        processing({ stage: 'page-split', recipe: spread, recipes: [spread] }),
        new Set(),
        halves,
      );

      await choose('all');

      expect(document.body.querySelector('[role="dialog"]')).toBeNull();
      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'spread' });
    });
  });
});
