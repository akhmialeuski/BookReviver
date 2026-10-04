import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { processing, recipe, step } from '@/features/processing/fixtures';
import { RunControls } from '@/features/processing/RunControls';
import { useStageRun } from '@/features/processing/useStageRun';
import { page, row } from '@/features/workspace/fixtures';
import { joinRows } from '@/features/workspace/strip';

/**
 * The foot of the panel: what it counts, and what the run sends for each of the pages it can go over.
 *
 * The generated client is replaced by a function the test reads, so the body of every run is seen as the server gets it.
 */

const sdk = vi.hoisted(() => ({ run: vi.fn(), impact: vi.fn(), jobs: vi.fn(), stages: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  runStageApiV1ProjectsProjectIdStagesStageRunPost: sdk.run,
  runImpactApiV1ProjectsProjectIdStagesStageRunImpactPost: sdk.impact,
  listProjectJobsApiV1ProjectsProjectIdJobsGet: sdk.jobs,
  listStagesApiV1ProjectsProjectIdStagesGet: sdk.stages,
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

/** The foot of the panel with the run it shares with the steps, as the panel builds them. */
function Foot({
  state,
  items,
  selected,
}: {
  state: ReturnType<typeof processing>;
  items: typeof ITEMS;
  selected: ReadonlySet<string>;
}): React.JSX.Element {
  const run = useStageRun(state, items, items[1], selected);
  return (
    <>
      <RunControls processing={state} items={items} run={run} />
      <button type="button" data-testid="through" onClick={() => run.start('all', 1)}>
        through
      </button>
    </>
  );
}

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
          <Foot state={state} items={items} selected={selected} />
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

  /** Choose what a run does with the pages that have work of their own, in the select above the run. */
  function chooseMode(mode: string): void {
    const select = container.querySelector<HTMLSelectElement>('[data-testid="run-mode"]');
    act(() => {
      if (select !== null) {
        select.value = mode;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  }

  function counts(mode: string, affected: number): void {
    sdk.impact.mockResolvedValue({
      data: { mode, pages: 4, hand_pages: affected, settings_pages: affected, affected },
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    sdk.run.mockReset();
    sdk.impact.mockReset();
    sdk.jobs.mockReset();
    sdk.stages.mockReset();
    sdk.stages.mockResolvedValue({ data: { items: [], total: 0, page: 1, size: 20, pages: 1 } });
    sdk.run.mockResolvedValue({ data: { id: 'job' } });
    sdk.impact.mockResolvedValue({
      data: { mode: 'replace-hand', pages: 4, hand_pages: 0, settings_pages: 0, affected: 0 },
    });
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
      body: { page_ids: ['b'] },
    });
  });

  it('runs the selected pages', async () => {
    render();

    await choose('selected');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['c', 'd'] });
  });

  it('runs the pages out of date and the pages that failed, and no others', async () => {
    render();

    await choose('attention');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });
  });

  it('runs every page by naming none, which the server reads as every page with an image', async () => {
    render();

    await choose('all');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({});
  });

  it('names no recipe for the active one, so each page gets the variant pinned to it or its rule', async () => {
    render();

    await choose('page');

    expect(sdk.run.mock.calls[0]?.[0].body).not.toHaveProperty('recipe_id');
  });

  it('runs the variant the panel shows on every page of the scope, as a trial that pins nothing', async () => {
    const variant = recipe('r2', { name: 'Gentle', active: false });
    render(processing({ recipe: variant, recipes: [recipe('r1'), variant] }));

    await choose('all');

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ recipe_id: 'r2' });
  });

  it('sends the index of the last step to run beside the pages, and names no recipe for the active one', async () => {
    render();

    await act(async () => {
      container.querySelector<HTMLElement>('[data-testid="through"]')?.click();
    });

    expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ through_step: 1 });
  });

  it('says how many pages stopped at each step, out of the steps of the recipe', async () => {
    const four = recipe('r1', { steps: [step('a'), step('b'), step('c'), step('d')] });
    sdk.stages.mockResolvedValue({
      data: {
        items: [
          {
            stage: 'geometry',
            available: true,
            manual: false,
            pages: 80,
            fresh: 80,
            stale: 0,
            failed: 0,
            not_run: 0,
            review: 0,
            check: 0,
            partial: 77,
            active_recipe_id: 'r1',
            variants: [],
            stopped: [
              { through_step: 0, pages: 1 },
              { through_step: 1, pages: 76 },
            ],
          },
        ],
        total: 1,
        page: 1,
        size: 20,
        pages: 1,
      },
    });
    render(processing({ recipe: four, recipes: [four] }));

    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="run-stopped"]')?.textContent).toBe(
        'Done through step 1 of 4: 1 pageDone through step 2 of 4: 76 pages',
      ),
    );
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
    // The jobs of the book are read, which takes a few turns of the queue, more of them on a busy machine
    await vi.waitFor(() =>
      expect(container.querySelector<HTMLButtonElement>('[data-testid="run-menu"]')?.disabled).toBe(
        true,
      ),
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

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ confirm_unsplit: true });
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
      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({});
    });
  });

  describe('the mode of a run', () => {
    it('keeps the work of the pages by default, which sends no mode and asks for no count', async () => {
      render();

      await choose('all');

      expect(sdk.impact).not.toHaveBeenCalled();
      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({});
    });

    it('offers the three modes, the usual one first', () => {
      render();

      const options = [
        ...container.querySelectorAll<HTMLOptionElement>('[data-testid="run-mode"] option'),
      ].map((option) => option.textContent);
      expect(options).toEqual([
        'Keep hand settings',
        'Replace hand settings',
        'Reset page settings',
      ]);
    });

    it('counts the pages a mode takes work from with the pages of the run, before it sends anything', async () => {
      counts('replace-hand', 3);
      render();
      chooseMode('replace-hand');

      await choose('selected');

      expect(sdk.impact.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', stage: 'geometry' },
        body: { mode: 'replace-hand', page_ids: ['c', 'd'] },
      });
      expect(sdk.run).not.toHaveBeenCalled();
    });

    it('warns with the number of pages that lose their hand settings', async () => {
      counts('replace-hand', 3);
      render();
      chooseMode('replace-hand');

      await choose('all');

      const dialog = document.body.querySelector('[data-testid="overwrite-dialog"]');
      expect(dialog?.textContent).toContain('Replace the hand settings?');
      expect(dialog?.querySelector('[data-testid="overwrite-pages"]')?.textContent).toContain(
        '3 pages lose the shape set by hand',
      );
      expect(dialog?.textContent).toContain('one undo gives it back');
    });

    it('warns with the number of pages that go back to the recipe when the settings are reset', async () => {
      counts('reset-page-settings', 1);
      render();
      chooseMode('reset-page-settings');

      await choose('all');

      expect(document.body.querySelector('[data-testid="overwrite-pages"]')?.textContent).toContain(
        '1 page goes back to the settings of the recipe',
      );
    });

    it('sends the run with the mode and the confirmation once the warning is accepted', async () => {
      counts('replace-hand', 3);
      render();
      chooseMode('replace-hand');
      await choose('all');

      await act(async () => {
        document.body.querySelector<HTMLElement>('[data-testid="overwrite-confirm"]')?.click();
      });

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
        mode: 'replace-hand',
        confirm_overwrite: true,
      });
    });

    it('sends nothing when the warning is declined', async () => {
      counts('replace-hand', 3);
      render();
      chooseMode('replace-hand');
      await choose('all');

      await act(async () => {
        const buttons = [...document.body.querySelectorAll<HTMLElement>('[role="dialog"] button')];
        buttons.find((button) => button.textContent === 'Cancel')?.click();
      });

      expect(sdk.run).not.toHaveBeenCalled();
      expect(document.body.querySelector('[data-testid="overwrite-dialog"]')).toBeNull();
    });

    it('sends the run with the mode and no question when no page has anything to lose', async () => {
      counts('replace-hand', 0);
      render();
      chooseMode('replace-hand');

      await choose('all');

      expect(document.body.querySelector('[role="dialog"]')).toBeNull();
      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ mode: 'replace-hand' });
    });
  });
});
