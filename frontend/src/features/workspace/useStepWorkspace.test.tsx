import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CarryOverSchema, FigureState, StagePageSchema, StepFlag } from '@/api';
import { deskew, processing, processor, recipe, step } from '@/features/processing/fixtures';
import { page, row, stepPage } from '@/features/workspace/fixtures';
import type { StripItem } from '@/features/workspace/strip';
import { type StepWorkspace, useStepWorkspace } from '@/features/workspace/useStepWorkspace';

/**
 * The workspace of the open step: which step the address names, what that step did on every page of the book, and the
 * state of each step on the open page.
 *
 * The generated client is replaced by a function the test reads, so the requests are seen as the server gets them: the
 * open step is read for the whole book, and every other step for the one row of the open page.
 */

const sdk = vi.hoisted(() => ({ rows: vi.fn() }));

vi.mock('@/api/sdk.gen', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sdk.gen')>()),
  listStagePagesApiV1ProjectsProjectIdStagesStagePagesGet: sdk.rows,
}));

const SAVED = recipe('r1', {
  steps: [
    step('geometry.perspective', { step_id: 'a' }),
    step('geometry.deskew', { step_id: 'b' }),
    step('geometry.deskew', { step_id: 'c' }),
  ],
});
const STATE = processing({
  catalogue: [processor('geometry.perspective', { title: 'Perspective' }), deskew()],
  recipes: [SAVED],
  recipe: SAVED,
});
// The second page of the book is open
const CURRENT: StripItem = { page: page('p2', { position: 1 }), row: row('p2') };

/** What a step says of a page. */
type States = Record<string, Record<string, FigureState>>;

function stepRow(stepId: string, pageId: string, state: FigureState): StagePageSchema {
  const flags: StepFlag[] =
    state === 'by-hand' ? ['by-hand'] : state === 'skipped' ? ['skipped'] : [];
  return row(pageId, { step: stepPage(stepId, state, { flags }) });
}

describe('useStepWorkspace', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let seen: StepWorkspace | undefined;

  /** What each step of the test book says of each of its pages. */
  const STATES: States = {
    a: { p1: 'default', p2: 'found', p3: 'default' },
    b: { p1: 'found', p2: 'by-hand', p3: 'skipped' },
    c: { p1: 'skipped', p2: 'skipped', p3: 'found' },
  };

  function Probe({
    stepId,
    state,
    current,
  }: {
    stepId: string | undefined;
    state: ReturnType<typeof processing>;
    current: StripItem;
  }): null {
    seen = useStepWorkspace(state, stepId, current);
    return null;
  }

  async function render(
    stepId: string | undefined,
    state = STATE,
    current = CURRENT,
  ): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <Probe stepId={stepId} state={state} current={current} />
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    seen = undefined;
    sdk.rows.mockReset();
    sdk.rows.mockImplementation(
      async ({ query }: { query: { step: string; page: number; size: number } }) => {
        const all = ['p1', 'p2', 'p3'].map((id) =>
          stepRow(query.step, id, STATES[query.step]?.[id] ?? 'default'),
        );
        const items = query.size === 1 ? all.slice(query.page - 1, query.page) : all;
        return { data: { items, total: 3, page: query.page, size: query.size, pages: 1 } };
      },
    );
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

  const requests = (): { step: string; page: number; size: number }[] =>
    sdk.rows.mock.calls.map(([options]) => options.query);

  it('reads the open step for the whole book and every other step for the one row of the open page', async () => {
    await render('b');

    expect(requests()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ step: 'b', size: 1000 }),
        { step: 'a', page: 2, size: 1 },
        { step: 'c', page: 2, size: 1 },
      ]),
    );
    expect(requests()).toHaveLength(3);
  });

  it('tells the state of every step on the open page', async () => {
    await render('b');

    expect([...(seen?.states ?? [])]).toEqual([
      ['a', 'found'],
      ['b', 'by-hand'],
      ['c', 'skipped'],
    ]);
  });

  it('names the open step and the page it did on the open page', async () => {
    await render('b');

    expect(seen?.open?.number).toBe(2);
    expect(seen?.page?.state).toBe('by-hand');
  });

  it('counts the pages of the book at the open step', async () => {
    await render('b');

    expect(seen?.counts).toEqual({
      found: 1,
      byHand: 1,
      skipped: 1,
      notRun: 0,
      check: 0,
      unusual: 0,
    });
  });

  it('moves to another step without reading the book again for the one it leaves', async () => {
    await render('b');
    await render('c');

    expect(seen?.open?.stepId).toBe('c');
    expect(seen?.page?.state).toBe('skipped');
    expect(seen?.counts).toEqual({
      found: 1,
      byHand: 0,
      skipped: 2,
      notRun: 0,
      check: 0,
      unusual: 0,
    });
    const wholeBook = requests().filter((query) => query.size === 1000);
    expect(wholeBook.map((query) => query.step)).toEqual(['b', 'c']);
  });

  it('reads one row of each step when no step is open, so the bar still has its dots', async () => {
    await render(undefined);

    expect(seen?.open).toBeNull();
    expect(seen?.counts).toBeNull();
    expect(requests().every((query) => query.size === 1)).toBe(true);
    expect([...(seen?.states ?? [])]).toEqual([
      ['a', 'found'],
      ['b', 'by-hand'],
      ['c', 'skipped'],
    ]);
  });

  it('opens no step for an address that names a step the recipe does not have', async () => {
    await render('gone');

    expect(seen?.open).toBeNull();
    expect(seen?.page).toBeNull();
  });

  it('leaves a step without a state while the row it was read for is of another page', async () => {
    // The pages were moved while the row was in flight: the row at the place is now another page's
    sdk.rows.mockImplementation(async ({ query }: { query: { step: string; size: number } }) => ({
      data: {
        items: [stepRow(query.step, 'moved', 'found')],
        total: 1,
        page: 1,
        size: query.size,
        pages: 1,
      },
    }));
    await render(undefined);

    expect([...(seen?.states ?? [])].map(([, state]) => state)).toEqual([null, null, null]);
  });

  it('gives the rows of the open step with the flags the server put on them, and none when no step is open', async () => {
    await render('b');
    const withStep = seen?.rows?.map((entry) => [entry.page_id, entry.step?.flags]);
    await render(undefined);

    expect(withStep).toEqual([
      ['p1', []],
      ['p2', ['by-hand']],
      ['p3', ['skipped']],
    ]);
    expect(seen?.rows).toBeNull();
  });

  it('has the bar of steps for Cleanup as it has for Geometry', async () => {
    const cleanup = recipe('c1', {
      stage: 'cleanup',
      steps: [
        step('cleanup.binarize', { step_id: 'a' }),
        step('cleanup.thickness', { step_id: 'b' }),
      ],
    });
    await render('b', {
      ...STATE,
      stage: 'cleanup',
      catalogue: [
        processor('cleanup.binarize', { title: 'Binarization', stage: 'cleanup' }),
        processor('cleanup.thickness', { title: 'Thickness', stage: 'cleanup' }),
      ],
      recipes: [cleanup],
      recipe: cleanup,
    });

    expect(seen?.steps.map((entry) => entry.title)).toEqual(['Binarization', 'Thickness']);
    expect(seen?.open?.title).toBe('Thickness');
    expect(requests()).toEqual(
      expect.arrayContaining([expect.objectContaining({ step: 'b', size: 1000 })]),
    );
  });

  describe('what a carry-over did', () => {
    // A carry-over that reached no page, which is enough to tell one result from another by identity
    const DONE = { batch_id: 'one', changes: [], skipped: [] } satisfies CarryOverSchema;
    const OTHER_PAGE: StripItem = { page: page('p3', { position: 2 }), row: row('p3') };

    it('stays with its step when the open page changes and changes back', async () => {
      await render('b');
      act(() => seen?.setCarried(DONE));
      expect(seen?.carried).toBe(DONE);

      await render('b', STATE, OTHER_PAGE);
      expect(seen?.carried).toBe(DONE);

      await render('b', STATE, CURRENT);
      expect(seen?.carried).toBe(DONE);
    });

    it('belongs to the step it was made on and to no other', async () => {
      await render('b');
      act(() => seen?.setCarried(DONE));

      await render('c');
      expect(seen?.carried).toBeNull();

      await render('b');
      expect(seen?.carried).toBe(DONE);
    });

    it('is forgotten once it is taken back', async () => {
      await render('b');
      act(() => seen?.setCarried(DONE));
      act(() => seen?.setCarried(null));

      expect(seen?.carried).toBeNull();
    });

    it('is not kept while no step is open', async () => {
      await render(undefined);
      act(() => seen?.setCarried(DONE));
      await render('b');

      expect(seen?.carried).toBeNull();
    });
  });

  it('reads nothing for a stage that has no bar of steps', async () => {
    await render('b', { ...STATE, stage: 'page-split' });

    expect(sdk.rows).not.toHaveBeenCalled();
    expect(seen?.steps).toEqual([]);
    expect(seen?.open).toBeNull();
  });
});
