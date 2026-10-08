import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageSchema, StagePageSchema } from '@/api';
import { deskew, processing, processor, recipe, step, whole } from '@/features/processing/fixtures';
import { RunControls } from '@/features/processing/RunControls';
import { useStageRun } from '@/features/processing/useStageRun';
import { page, row } from '@/features/workspace/fixtures';
import type { BarStep } from '@/features/workspace/steps';
import { joinRows } from '@/features/workspace/strip';

/**
 * The foot of the panel: what it counts, what its one button says, and what the run sends for each choice of its menu.
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
  openStep,
}: {
  state: ReturnType<typeof processing>;
  items: typeof ITEMS;
  selected: ReadonlySet<string>;
  openStep?: BarStep;
}): React.JSX.Element {
  const run = useStageRun(state, items, items[1], selected);
  return <RunControls processing={state} items={items} run={run} openStep={openStep} />;
}

const TWO_STEPS = recipe('r1', {
  steps: [step('geometry.deskew'), step('geometry.normalize')],
});

const OPEN_SECOND_STEP: BarStep = {
  stepId: 'second',
  number: 2,
  index: 1,
  title: 'Normalize',
  processorKey: 'geometry.normalize',
  enabled: true,
};

describe('RunControls', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  function render(
    state = processing(),
    selected: ReadonlySet<string> = new Set(['c', 'd']),
    items = ITEMS,
    openStep?: BarStep,
  ): void {
    act(() =>
      root.render(
        <QueryClientProvider client={client}>
          <Foot state={state} items={items} selected={selected} openStep={openStep} />
        </QueryClientProvider>,
      ),
    );
  }

  const text = (id: string): string | undefined =>
    container.querySelector(`[data-testid="${id}"]`)?.textContent ?? undefined;

  /** Open the menu of the run the way a keyboard does, which Radix answers in jsdom as it does in a browser. */
  async function openMenu(): Promise<void> {
    const trigger = container.querySelector<HTMLElement>('[data-testid="run-menu"]');
    await act(async () => {
      trigger?.focus();
      trigger?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
  }

  /** Choose an entry of the menu, which stays open for the next choice. */
  async function choose(testId: string): Promise<void> {
    if (document.body.querySelector('[role="menu"]') === null) {
      await openMenu();
    }
    await act(async () => {
      document.body.querySelector<HTMLElement>(`[data-testid="${testId}"]`)?.click();
    });
  }

  async function start(): Promise<void> {
    await act(async () => {
      container.querySelector<HTMLElement>('[data-testid="run-start"]')?.click();
    });
  }

  function owned(own: number, affected = own): void {
    sdk.impact.mockResolvedValue({
      data: {
        mode: 'keep',
        pages: 4,
        own_pages: own,
        affected,
      },
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
    owned(0);
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

  it('says the stage is running, with its progress, while a run of the stage is going', async () => {
    sdk.jobs.mockResolvedValue({
      data: {
        items: [
          {
            id: 'run',
            kind: 'run-stage',
            stage: processing().stage,
            state: 'running',
            progress: { done: 2, total: 4 },
          },
        ],
        total: 1,
        page: 1,
        size: 20,
        pages: 1,
      },
    });
    render(
      processing(),
      new Set(),
      joinRows(
        ITEMS.map((item) => item.page),
        [row('a'), row('b')],
      ),
    );

    // The pages read up to date while the run still places them, so the summary waits for the run to end
    await vi.waitFor(() =>
      expect(container.querySelector('[data-testid="run-summary"]')?.textContent).toBe(
        'Running the stage: 2 of 4',
      ),
    );
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
            recipes: [{ kind: 'text', recipe_id: 'r1', pages: 80 }],
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

  describe('the button', () => {
    it('runs the pages out of date and failed again by default, since the page that is out of date has a result', async () => {
      render();

      expect(text('run-start')).toBe('Run again on 2 out-of-date and failed pages through Deskew');

      await start();

      expect(sdk.run.mock.calls[0]?.[0]).toMatchObject({
        path: { project_id: 'project', stage: 'geometry' },
        body: { page_ids: ['b', 'c'] },
      });
    });

    it('says Run, and not Run again, when none of the pages has a result yet', async () => {
      render();

      await choose('run-pages-selected');

      expect(text('run-start')).toBe('Run on 2 selected pages through Deskew');
    });

    it('stands alone in the foot, with the menu beside it and no preview of the page or Auto', () => {
      render();

      const buttons = [...container.querySelectorAll('button')].map((button) =>
        button.getAttribute('data-testid'),
      );
      expect(buttons).toEqual(['run-start', 'run-menu']);
      expect(container.textContent).not.toMatch(/Preview|Auto/);
      expect(container.querySelector('[data-testid="run-summary"]')).not.toBeNull();
    });

    it('is off while the draft has changes that are not saved', () => {
      render(processing({ dirty: true }));

      expect(
        container.querySelector<HTMLButtonElement>('[data-testid="run-start"]')?.disabled,
      ).toBe(true);
      expect(container.textContent?.match(/Save the recipe to run it\./g)).toHaveLength(1);
    });

    it('is off while another job of the book is going', async () => {
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
        expect(
          container.querySelector<HTMLButtonElement>('[data-testid="run-start"]')?.disabled,
        ).toBe(true),
      );
    });

    it('is off when the choice covers no page', async () => {
      render(
        processing(),
        new Set(),
        joinRows(
          ITEMS.map((item) => item.page),
          [row('a'), row('b')],
        ),
      );

      expect(
        container.querySelector<HTMLButtonElement>('[data-testid="run-start"]')?.disabled,
      ).toBe(true);
    });
  });

  describe('the text of the button', () => {
    /** The four pages of the book, a to d, with the status each has in the stage. */
    const withStatuses = (
      statuses: readonly StagePageSchema['status'][],
      overrides: Partial<PageSchema> = {},
    ): typeof ITEMS =>
      joinRows(
        ['a', 'b', 'c', 'd'].map((id, position) => page(id, { position, ...overrides })),
        ['a', 'b', 'c', 'd'].map((id, place) => row(id, { status: statuses[place] ?? 'not-run' })),
      );

    // Every page of these has a result, the second and the third out of date, and none of those has
    const RESULTS = ['fresh', 'stale', 'stale', 'fresh'] as const;
    const NO_RESULT = ['not-run', 'failed', 'failed', 'not-run'] as const;
    const ONE_RESULT = ['fresh', 'not-run', 'not-run', 'not-run'] as const;

    const BOTH = new Set(['c', 'd']);
    const ONE = new Set(['c']);

    // The page open is b, and the recipe has two steps, of which the open step in the bar is the first
    const state = (): ReturnType<typeof processing> =>
      processing({
        recipe: TWO_STEPS,
        recipes: [TWO_STEPS],
        catalogue: [deskew(), processor('geometry.normalize', { title: 'Normalize' })],
      });
    const OPEN_FIRST_STEP: BarStep = { ...OPEN_SECOND_STEP, index: 0, number: 1, title: 'Deskew' };

    // The pages of the choice, the statuses of the book, who is selected, and what the button says about the pages
    const CHOICES = [
      ['page', RESULTS, BOTH, 'Run again on this page'],
      ['from-page', RESULTS, BOTH, 'Run again on 3 pages from this page on'],
      ['selected', RESULTS, BOTH, 'Run again on 2 selected pages'],
      ['selected', RESULTS, ONE, 'Run again on 1 selected page'],
      ['group:text', RESULTS, BOTH, 'Run again on 4 pages of the kind Text'],
      ['attention', RESULTS, BOTH, 'Run again on 2 out-of-date pages'],
      ['all', RESULTS, BOTH, 'Run again on all 4 pages'],
      ['page', NO_RESULT, BOTH, 'Run on this page'],
      ['from-page', NO_RESULT, BOTH, 'Run on 3 pages from this page on'],
      ['selected', NO_RESULT, BOTH, 'Run on 2 selected pages'],
      ['group:text', NO_RESULT, BOTH, 'Run on 4 pages of the kind Text'],
      ['attention', NO_RESULT, BOTH, 'Run on 2 failed pages'],
      ['all', NO_RESULT, BOTH, 'Run on all 4 pages'],
      // The words follow the pages the run goes over, and not the book: a is the only page with a result
      ['all', ONE_RESULT, BOTH, 'Run again on all 4 pages'],
      ['selected', ONE_RESULT, BOTH, 'Run on 2 selected pages'],
    ] as const;

    const THROUGH = [
      ['up to the open step', 'run-through-open', 'Deskew'],
      ['through the whole stage', 'run-through-stage', 'Normalize'],
    ] as const;

    it.each(
      CHOICES.flatMap(([key, statuses, selected, pages]) =>
        THROUGH.map(([how, through, step]) => ({
          name: `${key} of ${statuses.join(', ')}, ${[...selected].join('')} selected, ${how}`,
          key,
          statuses,
          selected,
          through,
          text: `${pages} through ${step}`,
        })),
      ),
    )('says $text for $name', async ({ key, statuses, selected, through, text: expected }) => {
      render(state(), selected, withStatuses(statuses), OPEN_FIRST_STEP);

      await choose(`run-pages-${key}`);
      await choose(through);

      expect(text('run-start')).toBe(expected);
    });
  });

  describe('the pages of the menu', () => {
    it.each([
      ['page', { page_ids: ['b'] }],
      ['from-page', { page_ids: ['b', 'c', 'd'] }],
      ['selected', { page_ids: ['c', 'd'] }],
      ['group:text', { page_ids: ['a', 'b', 'c', 'd'] }],
      ['attention', { page_ids: ['b', 'c'] }],
      ['all', {}],
    ])('runs %s', async (key, body) => {
      render();

      await choose(`run-pages-${key}`);
      await start();

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual(body);
    });

    it('lists a kind of pages only when the book has some, with its count', async () => {
      render(processing(), new Set(), joinRows([page('a'), page('b', { kind: 'blank' })], []));

      await openMenu();

      expect(
        document.body.querySelector('[data-testid="run-pages-group:blank"]')?.textContent,
      ).toBe('Pages of a kind · Blank1');
      expect(document.body.querySelector('[data-testid="run-pages-group:bw-picture"]')).toBeNull();
    });

    it('turns Selected pages off while no page is selected, and on once one is', async () => {
      render(processing(), new Set());
      await openMenu();

      const entry = (): Element | null =>
        document.body.querySelector('[data-testid="run-pages-selected"]');
      expect(entry()?.getAttribute('aria-disabled')).toBe('true');
      expect(entry()?.textContent).toBe('Selected pages0');

      render(processing(), new Set(['c']));

      expect(entry()?.getAttribute('aria-disabled')).toBeNull();
      expect(entry()?.textContent).toBe('Selected pages1');
    });

    it('lists every kind of pages the book has, with its count, in the order of the menu', async () => {
      render(
        processing(),
        new Set(),
        joinRows(
          [
            page('a'),
            page('b'),
            page('c', { content_type: 'bw-picture' }),
            page('d', { content_type: 'color-picture' }),
            page('e', { kind: 'blank' }),
          ],
          [],
        ),
      );

      await openMenu();

      const kinds = [...document.body.querySelectorAll('[data-testid^="run-pages-group:"]')];
      expect(kinds.map((kind) => kind.textContent)).toEqual([
        'Pages of a kind · Text3',
        'Pages of a kind · Colour picture1',
        'Pages of a kind · Black-and-white picture1',
        'Pages of a kind · Blank1',
      ]);
    });

    it('names no recipe, so each page is run by the recipe of its kind', async () => {
      render();

      await start();

      expect(sdk.run.mock.calls[0]?.[0].body).not.toHaveProperty('recipe_id');
    });
  });

  describe('how far the run goes', () => {
    const state = (): ReturnType<typeof processing> =>
      processing({
        recipe: TWO_STEPS,
        recipes: [TWO_STEPS],
        catalogue: [deskew(), whole()],
      });

    it('goes through the last step of the stage by default, which sends no step', async () => {
      render(state(), new Set(), ITEMS, OPEN_SECOND_STEP);

      await start();

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });
    });

    it('stops at the step that is open when asked, counting the steps from zero', async () => {
      render(state(), new Set(), ITEMS, { ...OPEN_SECOND_STEP, index: 0, number: 1 });

      await choose('run-through-open');
      await start();

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'], through_step: 0 });
    });

    it('offers nothing to choose for a recipe of one step', async () => {
      render(processing(), new Set(), ITEMS, OPEN_SECOND_STEP);

      await openMenu();

      expect(document.body.querySelector('[data-testid="run-through-open"]')).toBeNull();
    });
  });

  describe('the pages with work of their own', () => {
    it('is not in the menu while no page has any', async () => {
      render();

      await openMenu();

      expect(document.body.querySelector('[data-testid="run-own-keep"]')).toBeNull();
    });

    it('counts the pages that have some, and keeps their work by default, which sends no mode', async () => {
      owned(2);
      render();
      await openMenu();

      await vi.waitFor(() =>
        expect(document.body.querySelector('[data-testid="run-own-keep"]')).not.toBeNull(),
      );
      expect(document.body.textContent).toContain('Pages with work of their own · 2 among them');
      expect(sdk.impact.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });

      await start();

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['b', 'c'] });
    });

    it('leaves them out with a mode of the run and no warning, since the run takes nothing away', async () => {
      owned(2);
      render();
      await openMenu();
      await vi.waitFor(() =>
        expect(document.body.querySelector('[data-testid="run-own-skip-own-work"]')).not.toBeNull(),
      );

      await choose('run-own-skip-own-work');

      expect(text('run-start')).toContain('0 out-of-date and failed pages');
      expect(
        container.querySelector<HTMLButtonElement>('[data-testid="run-start"]')?.disabled,
      ).toBe(true);
    });

    it('warns with the number of pages that lose their work when it is dropped, and sends the run once it is accepted', async () => {
      owned(2);
      render();
      await openMenu();
      await vi.waitFor(() =>
        expect(document.body.querySelector('[data-testid="run-own-drop-own-work"]')).not.toBeNull(),
      );
      await choose('run-own-drop-own-work');

      await start();

      expect(document.body.querySelector('[data-testid="overwrite-pages"]')?.textContent).toContain(
        '2 pages lose',
      );
      expect(sdk.run).not.toHaveBeenCalled();

      await act(async () => {
        document.body.querySelector<HTMLElement>('[data-testid="overwrite-confirm"]')?.click();
      });

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
        page_ids: ['b', 'c'],
        mode: 'drop-own-work',
        confirm_overwrite: true,
      });
    });

    it('sends nothing when the warning is declined', async () => {
      owned(2);
      render();
      await openMenu();
      await vi.waitFor(() =>
        expect(document.body.querySelector('[data-testid="run-own-drop-own-work"]')).not.toBeNull(),
      );
      await choose('run-own-drop-own-work');
      await start();

      await act(async () => {
        const buttons = [...document.body.querySelectorAll<HTMLElement>('[role="dialog"] button')];
        buttons.find((button) => button.textContent === 'Cancel')?.click();
      });

      expect(sdk.run).not.toHaveBeenCalled();
    });
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

      await start();

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
      await start();

      await act(async () => {
        document.body.querySelector<HTMLElement>('[data-testid="unsplit-confirm"]')?.click();
      });

      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({
        page_ids: ['l', 'r'],
        confirm_unsplit: true,
      });
    });

    it('sends nothing when the answer is no', async () => {
      render(
        processing({ stage: 'page-split', recipe: whole, recipes: [whole] }),
        new Set(),
        halves,
      );
      await start();

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

      await start();

      expect(document.body.querySelector('[role="dialog"]')).toBeNull();
      expect(sdk.run.mock.calls[0]?.[0].body).toEqual({ page_ids: ['l', 'r'] });
    });
  });
});
